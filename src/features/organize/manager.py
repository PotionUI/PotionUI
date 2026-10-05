import copy
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from src.features.organize import errors
from src.features.organize.core_actions import ADD_TAGS, ADD_TO_COLLECTION, collection_target_type, resolve_collection
from src.features.organize.evaluator import as_list, canonical_conditions, evaluate, sql_filter
from src.features.organize.item_repository import OrganizeItemRepository
from src.features.organize.jobs import BackfillJob, JobBook
from src.features.organize.records import COLLECTION_SCOPES, SUBJECT_TRIGGERS, Controls, Rule, Run
from src.features.organize.rule_repository import GLOBAL_SCOPE, OrganizeRuleRepository, RuleCapReached
from src.features.organize.run_repository import OrganizeRunRepository
from src.features.organize.templates import list_templates
from src.features.organize.validation import problem, validate_name, validate_rule_shape
from src.features.organize.visibility import OrganizeVisibility
from src.features.organize.write_repository import OrganizeWriteRepository
from src.platform.database.rows import dt_column, dt_iso
from src.platform.plugins.organize import (
    FACT_KINDS,
    OPERATOR_LABELS,
    OrganizeActionBlocked,
    OrganizeChange,
    OrganizeItem,
    OrganizeRegistry,
)

logger = logging.getLogger(__name__)

DEFAULT_RULE_CAP = 50
DEFAULT_HOURLY_LIMIT = 200
BACKFILL_CHUNK = 500
PREVIEW_SCAN_CAP = 5000
SAMPLE_SIZE = 12
OPTIONS_TIMEOUT_S = 2.0

NOTICE_RULE_PAUSED = "organize.rule_paused"
NOTICE_JOB_PROGRESS = "organize.job_progress"
NOTICE_JOB_FINISHED = "organize.job_finished"

SUBJECT_LABELS = {
    "generation": ("Generations", "When a new generation completes", True),
    "upload": ("Library uploads", "When I upload a file", True),
    "model": ("Models", "When a model is added", False),
}

PAUSE_MESSAGES = {
    "rate_limited": "\"{name}\" filed more than {limit} items in an hour, so it was paused. Check it and switch it back on.",
    "collection_missing": "\"{name}\" was paused because the collection it adds to no longer exists.",
    "model_restricted": "\"{name}\" was paused because it uses a model your account can no longer use.",
}

BLOCKING_CODES = {"fact_unavailable", "action_unavailable", "collection_missing", "model_restricted"}


@dataclass
class OrganizeCollaborators:
    rules: OrganizeRuleRepository
    runs: OrganizeRunRepository
    items: OrganizeItemRepository
    writer: OrganizeWriteRepository
    registry: OrganizeRegistry
    visibility: OrganizeVisibility
    notify: Optional[Callable[..., Any]] = None


@dataclass
class Viewer:
    id: str
    is_admin: bool


def viewer_of(user: Any) -> Viewer:
    account_type = getattr(user, "account_type", None)
    value = getattr(account_type, "value", account_type)
    return Viewer(id=user.id, is_admin=str(value).upper() == "ADMIN")


class OrganizeManager:

    def __init__(self, collaborators: OrganizeCollaborators, executor: Optional[Any] = None):
        self.c = collaborators
        self.jobs = JobBook()
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="organize-backfill")
        self._options_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="organize-options")

    def shutdown(self) -> None:
        self.jobs.cancel_all()
        shutdown = getattr(self._executor, "shutdown", None)
        if shutdown:
            shutdown(wait=False)
        self._options_pool.shutdown(wait=False)

    def global_controls(self) -> Controls:
        return self.c.rules.controls(GLOBAL_SCOPE)

    def hourly_limit(self) -> int:
        return self.global_controls().hourly_limit or DEFAULT_HOURLY_LIMIT

    def default_cap(self) -> int:
        cap = self.global_controls().rule_cap
        return DEFAULT_RULE_CAP if cap is None else cap

    def effective_cap(self, user_id: str) -> int:
        user_cap = self.c.rules.controls(user_id).rule_cap
        return self.default_cap() if user_cap is None else user_cap

    def is_paused_for(self, user_id: str) -> bool:
        return self.global_controls().paused or self.c.rules.controls(user_id).paused

    def catalog(self, user: Any, subject: Optional[str] = None) -> Dict[str, Any]:
        viewer = viewer_of(user)
        facts = [
            {
                "key": f.key,
                "label": f.label,
                "subjects": list(f.subjects),
                "kind": f.kind,
                "operators": list(f.allowed_operators()),
                "picker": dict(f.picker),
                "options": [dict(o) for o in f.options] or None,
                "has_options_endpoint": f.options_handler is not None,
                "description": f.description,
                "source": f.source,
                "component": f.component,
                "previewable": f.sql is not None,
            }
            for f in self.c.registry.facts(subject)
        ]
        actions = [
            {
                "key": a.key,
                "label": a.label,
                "subjects": list(a.subjects),
                "config_schema": [dict(entry) for entry in a.config_schema],
                "requires_admin": a.requires_admin,
                "undoable": a.undo is not None,
                "description": a.description,
                "source": a.source,
                "component": a.component,
            }
            for a in self.c.registry.actions(subject)
            if viewer.is_admin or not a.requires_admin
        ]
        subjects = [
            {
                "key": key,
                "label": label,
                "trigger": SUBJECT_TRIGGERS[key],
                "trigger_label": trigger_label,
                "collection_scope": COLLECTION_SCOPES[key],
                "supports_tags": supports_tags,
            }
            for key, (label, trigger_label, supports_tags) in SUBJECT_LABELS.items()
        ]
        return {
            "subjects": subjects,
            "kinds": {kind: {"operators": list(ops)} for kind, ops in FACT_KINDS.items()},
            "operators": dict(OPERATOR_LABELS),
            "facts": facts,
            "actions": actions,
        }

    def fact_options(self, user: Any, key: str, subject: Optional[str], query: str, limit: int) -> List[Dict[str, str]]:
        fact = self.c.registry.fact(key)
        if fact is None:
            raise errors.OrganizeError("fact_not_found", "That field is not available", 404)
        subject = subject if subject in fact.subjects else fact.subjects[0]
        limit = max(1, min(limit, 200))
        if fact.options_handler is None:
            if not fact.options:
                raise errors.OrganizeError("no_options", "This field has no list of choices", 400)
            needle = (query or "").casefold()
            return [
                {"value": str(o.get("value")), "label": str(o.get("label", o.get("value")))}
                for o in fact.options
                if needle in str(o.get("label", "")).casefold() or needle in str(o.get("value", "")).casefold()
            ][:limit]
        return self._handler_options(fact, user.id, subject, query)[:limit]

    def _handler_options(self, fact: Any, user_id: str, subject: str, query: str) -> List[Dict[str, Any]]:
        try:
            if fact.source == "core":
                raw = fact.options_handler(user_id, subject, query or "")
            else:
                raw = self._options_pool.submit(fact.options_handler, user_id, subject, query or "").result(
                    timeout=OPTIONS_TIMEOUT_S
                )
        except FutureTimeout:
            logger.warning("Auto-organize options for '%s' timed out", fact.key)
            return []
        except Exception:
            logger.warning("Auto-organize options for '%s' failed", fact.key, exc_info=True)
            return []
        options: List[Dict[str, Any]] = []
        for entry in raw or []:
            if isinstance(entry, dict) and entry.get("value") is not None:
                option: Dict[str, Any] = {"value": str(entry["value"]), "label": str(entry.get("label", entry["value"]))}
                if isinstance(entry.get("meta"), dict):
                    option["meta"] = dict(entry["meta"])
                options.append(option)
            elif isinstance(entry, str):
                options.append({"value": entry, "label": entry})
        return options

    def _attribute_specs_for(self, viewer: Viewer) -> Callable[[Any, str], Dict[str, Dict[str, Any]]]:
        def specs(fact: Any, subject: str) -> Dict[str, Dict[str, Any]]:
            if fact.options_handler is None:
                return {}
            return {
                option["value"]: {**option.get("meta", {}), "label": option["label"]}
                for option in self._handler_options(fact, viewer.id, subject, "")
            }

        return specs

    def templates(self, subject: Optional[str]) -> List[Dict[str, Any]]:
        return copy.deepcopy(list_templates(subject))

    def summary(self, user: Any) -> Dict[str, Any]:
        viewer = viewer_of(user)
        paused = self.is_paused_for(viewer.id)
        described = self.describe(viewer, self.c.rules.list_for_user(viewer.id))
        subjects = {key: {"total": 0, "active": 0, "needs_attention": 0} for key in SUBJECT_TRIGGERS}
        for rule in described:
            bucket = subjects[rule["subject"]]
            bucket["total"] += 1
            if rule["status"] == "active" and not paused:
                bucket["active"] += 1
            elif rule["status"] in ("paused", "needs_attention") and rule["paused_reason"] != "admin_paused":
                bucket["needs_attention"] += 1
        return {
            "paused_by_admin": paused,
            "rule_cap": self.effective_cap(viewer.id),
            "rule_count": len(described),
            "hourly_limit": self.hourly_limit(),
            "subjects": subjects,
        }

    def list_rules(self, user: Any, subject: Optional[str]) -> List[Dict[str, Any]]:
        viewer = viewer_of(user)
        return self.describe(viewer, self.c.rules.list_for_user(viewer.id, subject))

    def get_rule(self, user: Any, rule_id: str) -> Dict[str, Any]:
        viewer = viewer_of(user)
        return self.describe(viewer, [self._require_rule(viewer.id, rule_id)])[0]

    def _require_rule(self, user_id: str, rule_id: str) -> Rule:
        rule = self.c.rules.get(user_id, rule_id)
        if rule is None:
            raise errors.rule_not_found()
        return rule

    def create_rule(self, user: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        viewer = viewer_of(user)
        name, conditions, actions = self._validated(viewer, payload, payload.get("subject"), previous=None)
        cap = self.effective_cap(viewer.id)
        subject = payload["subject"]
        rule = Rule(
            id="", user_id=viewer.id, name=name, subject=subject, trigger=SUBJECT_TRIGGERS[subject],
            match=payload.get("match") or "all", conditions=conditions, actions=actions,
            enabled=bool(payload.get("enabled", True)), stop_after=bool(payload.get("stop_after", False)),
        )
        if self.c.rules.count_for_user(viewer.id) >= cap:
            raise errors.rule_cap_reached(cap)
        actions = self._prepare_targets(viewer, subject, actions)
        rule.actions = actions
        try:
            created = self.c.rules.create(rule, cap)
        except RuleCapReached:
            raise errors.rule_cap_reached(cap)
        return self.describe(viewer, [created])[0]

    def update_rule(self, user: Any, rule_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        viewer = viewer_of(user)
        rule = self._require_rule(viewer.id, rule_id)
        if payload.get("subject") not in (None, rule.subject):
            raise errors.invalid_rule([problem("subject", "subject_locked", "A rule's subject cannot change")])
        merged = {
            "name": rule.name, "match": rule.match, "conditions": rule.conditions, "actions": rule.actions,
            "enabled": rule.enabled, "stop_after": rule.stop_after,
        }
        merged.update({k: v for k, v in payload.items() if v is not None})
        merged["subject"] = rule.subject
        name, conditions, actions = self._validated(viewer, merged, rule.subject, previous=rule)
        actions = self._prepare_targets(viewer, rule.subject, actions)
        reset = self._action_signature(rule.actions) != self._action_signature(actions)
        rule.name = name
        rule.match = merged["match"]
        rule.conditions = conditions
        rule.actions = actions
        rule.enabled = bool(merged["enabled"])
        rule.stop_after = bool(merged["stop_after"])
        rule.paused_reason = None
        rule.paused_at = None
        updated = self.c.rules.update(rule, reset_handled=reset)
        if updated is None:
            raise errors.rule_not_found()
        return self.describe(viewer, [updated])[0]

    def patch_rule(self, user: Any, rule_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        viewer = viewer_of(user)
        rule = self._require_rule(viewer.id, rule_id)
        if payload.get("name") is not None:
            name, problems = validate_name(payload["name"])
            if problems:
                raise errors.invalid_rule(problems)
            rule.name = name
        if payload.get("stop_after") is not None:
            rule.stop_after = bool(payload["stop_after"])
        if payload.get("enabled") is not None:
            rule.enabled = bool(payload["enabled"])
            if rule.enabled:
                rule.paused_reason = None
                rule.paused_at = None
        updated = self.c.rules.update(rule)
        if updated is None:
            raise errors.rule_not_found()
        return self.describe(viewer, [updated])[0]

    def delete_rule(self, user: Any, rule_id: str) -> Dict[str, Any]:
        viewer = viewer_of(user)
        if not self.c.rules.delete(viewer.id, rule_id):
            raise errors.rule_not_found()
        return {"deleted": True}

    def reorder(self, user: Any, subject: str, rule_ids: Sequence[str]) -> List[Dict[str, Any]]:
        viewer = viewer_of(user)
        current = {r.id for r in self.c.rules.list_for_user(viewer.id, subject)}
        if subject not in SUBJECT_TRIGGERS or len(rule_ids) != len(set(rule_ids)) or set(rule_ids) != current:
            raise errors.OrganizeError("invalid_order", "List every rule of this kind exactly once", 422)
        self.c.rules.reorder(viewer.id, subject, list(rule_ids))
        return self.list_rules(user, subject)

    def duplicate(self, user: Any, rule_id: str) -> Dict[str, Any]:
        viewer = viewer_of(user)
        rule = self._require_rule(viewer.id, rule_id)
        cap = self.effective_cap(viewer.id)
        copy_rule = Rule(
            id="", user_id=viewer.id, name=f"{rule.name} (copy)"[:120], subject=rule.subject, trigger=rule.trigger,
            match=rule.match, conditions=copy.deepcopy(rule.conditions), actions=copy.deepcopy(rule.actions),
            enabled=False, stop_after=rule.stop_after,
        )
        try:
            created = self.c.rules.create(copy_rule, cap)
        except RuleCapReached:
            raise errors.rule_cap_reached(cap)
        return self.describe(viewer, [created])[0]

    def _action_signature(self, actions: List[Dict[str, Any]]) -> str:
        trimmed = []
        for action in actions:
            config = dict(action.get("config") or {})
            if action.get("action") == ADD_TO_COLLECTION:
                config.pop("collection_name", None)
            trimmed.append({"action": action.get("action"), "config": config})
        return json.dumps(trimmed, sort_keys=True)

    def _validated(self, viewer: Viewer, payload: Dict[str, Any], subject: Any,
                   previous: Optional[Rule]) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
        name, problems = validate_name(payload.get("name"))
        shape, shape_problems = validate_rule_shape(
            self.c.registry, subject, payload.get("match") or "all", payload.get("conditions") or [],
            payload.get("actions"), viewer.is_admin, attribute_specs=self._attribute_specs_for(viewer),
        )
        problems.extend(shape_problems)
        if not problems:
            problems.extend(self._model_problems(viewer, shape["conditions"], previous))
            problems.extend(self._target_problems(viewer, subject, shape["actions"]))
        if problems:
            raise errors.invalid_rule(problems)
        return name, shape["conditions"], shape["actions"]

    def _model_refs(self, conditions: Iterable[Dict[str, Any]]) -> List[Tuple[int, str]]:
        refs = []
        for index, condition in enumerate(conditions):
            fact = self.c.registry.fact(condition.get("fact", ""))
            if fact is not None and fact.kind == "model_ref":
                refs.extend((index, str(v)) for v in as_list(condition.get("value")))
        return refs

    def _model_problems(self, viewer: Viewer, conditions: List[Dict[str, Any]], previous: Optional[Rule]) -> List[Dict[str, str]]:
        refs = self._model_refs(conditions)
        if not refs:
            return []
        ids = [model_id for _, model_id in refs]
        restricted = self.c.visibility.is_restricted(viewer.id)
        visible = self.c.items.visible_model_ids(viewer.id, ids, viewer.is_admin, restricted)
        existing = self.c.items.existing_model_ids(ids)
        kept = {model_id for _, model_id in self._model_refs(previous.conditions)} if previous else set()
        problems = []
        for index, model_id in refs:
            if model_id in visible:
                continue
            if model_id not in existing and model_id in kept:
                continue
            problems.append(problem(f"conditions.{index}.value", "model_not_allowed", "You cannot use that model"))
        return problems

    def _target_problems(self, viewer: Viewer, subject: str, actions: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        scope = COLLECTION_SCOPES[subject]
        problems = []
        for index, action in enumerate(actions):
            if action["action"] != ADD_TO_COLLECTION:
                continue
            config = action["config"]
            collection_id = config.get("collection_id")
            if collection_id and self.c.writer.find_collection(scope, collection_id, viewer.id) is None:
                if not (config.get("collection_name") and config.get("create_if_missing", True)):
                    problems.append(problem(
                        f"actions.{index}.config.collection_id", "collection_not_found", "That collection does not exist"
                    ))
            parent_id = config.get("parent_id")
            if parent_id and self.c.writer.find_collection(scope, parent_id, viewer.id) is None:
                problems.append(problem(
                    f"actions.{index}.config.parent_id", "collection_not_found", "That collection does not exist"
                ))
        return problems

    def _prepare_targets(self, viewer: Viewer, subject: str, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        prepared = []
        for action in actions:
            action = copy.deepcopy(action)
            if action["action"] == ADD_TO_COLLECTION:
                config = action["config"]
                if config.get("create_if_missing") is None:
                    config["create_if_missing"] = True
                collection, _created = resolve_collection(self.c.writer, subject, dict(config, create_if_missing=True), viewer.id)
                config["collection_id"] = collection.id
                config["collection_name"] = collection.name
                config.setdefault("parent_id", None)
            prepared.append(action)
        return prepared

    def describe(self, viewer: Viewer, rules: List[Rule]) -> List[Dict[str, Any]]:
        if not rules:
            return []
        admin_paused = self.is_paused_for(viewer.id)
        restricted = self.c.visibility.is_restricted(viewer.id)
        collection_ids: Dict[str, Set[str]] = {}
        model_ids: Set[str] = set()
        for rule in rules:
            for action in rule.actions:
                if action.get("action") == ADD_TO_COLLECTION and action.get("config", {}).get("collection_id"):
                    collection_ids.setdefault(COLLECTION_SCOPES[rule.subject], set()).add(action["config"]["collection_id"])
            model_ids.update(model_id for _, model_id in self._model_refs(rule.conditions))
        names = {
            scope: self.c.writer.collection_names(scope, ids, viewer.id) for scope, ids in collection_ids.items()
        }
        existing_models = self.c.items.existing_model_ids(model_ids) if model_ids else set()
        nsfw = self.c.items.nsfw_model_ids(model_ids) if model_ids and restricted else set()
        model_names = self.c.items.model_names(model_ids) if model_ids else {}
        filed = self.c.runs.filed_counts(viewer.id, [r.id for r in rules])
        described = []
        for rule in rules:
            issues = self._issues(viewer, rule, names.get(COLLECTION_SCOPES[rule.subject], {}), existing_models, nsfw, model_names)
            paused_reason = rule.paused_reason
            if paused_reason is None and admin_paused and rule.enabled:
                paused_reason = "admin_paused"
            if not rule.enabled:
                status = "off"
            elif paused_reason:
                status = "paused"
            elif any(issue["blocking"] for issue in issues):
                status = "needs_attention"
            else:
                status = "active"
            targets = []
            for action in rule.actions:
                if action.get("action") == ADD_TO_COLLECTION:
                    config = action.get("config", {})
                    collection_id = config.get("collection_id")
                    current = names.get(COLLECTION_SCOPES[rule.subject], {}).get(collection_id)
                    targets.append({
                        "id": collection_id,
                        "name": current or config.get("collection_name") or "",
                        "exists": current is not None,
                    })
            described.append({
                "id": rule.id,
                "name": rule.name,
                "subject": rule.subject,
                "trigger": rule.trigger,
                "match": rule.match,
                "conditions": rule.conditions,
                "actions": rule.actions,
                "enabled": rule.enabled,
                "stop_after": rule.stop_after,
                "position": rule.position,
                "status": status,
                "paused_reason": paused_reason,
                "paused_at": dt_iso(rule.paused_at),
                "issues": issues,
                "targets": {"collections": targets},
                "filed_count": filed.get(rule.id, 0),
                "last_run_at": dt_iso(rule.last_run_at),
                "created_at": dt_iso(rule.created_at),
                "updated_at": dt_iso(rule.updated_at),
            })
        return described

    def _issues(self, viewer: Viewer, rule: Rule, collection_names: Dict[str, str], existing_models: Set[str],
                nsfw: Set[str], model_names: Dict[str, str]) -> List[Dict[str, Any]]:
        issues = []
        for index, condition in enumerate(rule.conditions):
            fact = self.c.registry.fact(condition.get("fact", ""))
            path = f"conditions.{index}"
            if fact is None:
                issues.append({"code": "fact_unavailable", "message": "A field this rule checks is no longer available",
                               "blocking": True, "path": path})
                continue
            if fact.kind != "model_ref":
                continue
            for model_id in as_list(condition.get("value")):
                if model_id in nsfw:
                    issues.append({"code": "model_restricted", "message": "This rule uses a model your account cannot use",
                                   "blocking": True, "path": path})
                elif model_id not in existing_models:
                    label = model_names.get(model_id) or "A model this rule checks"
                    issues.append({"code": "model_missing", "message": f"{label} is no longer installed",
                                   "blocking": False, "path": path})
        for index, action in enumerate(rule.actions):
            definition = self.c.registry.action(action.get("action", ""))
            path = f"actions.{index}"
            if definition is None or (definition.requires_admin and not viewer.is_admin):
                issues.append({"code": "action_unavailable", "message": "Something this rule does is no longer available",
                               "blocking": True, "path": path})
                continue
            if definition.key == ADD_TO_COLLECTION:
                config = action.get("config", {})
                if config.get("collection_id") not in collection_names:
                    if config.get("create_if_missing", True) and config.get("collection_name"):
                        issues.append({"code": "collection_missing",
                                       "message": f"{config['collection_name']} was removed and will be created again",
                                       "blocking": False, "path": path})
                    else:
                        issues.append({"code": "collection_missing",
                                       "message": "The collection this rule adds to no longer exists",
                                       "blocking": True, "path": path})
        return issues

    def _blocking_reason(self, rule: Rule, user_id: str, is_admin: bool, restricted: bool) -> Optional[str]:
        for condition in rule.conditions:
            if self.c.registry.fact(condition.get("fact", "")) is None:
                return "fact_unavailable"
        for action in rule.actions:
            definition = self.c.registry.action(action.get("action", ""))
            if definition is None or (definition.requires_admin and not is_admin):
                return "action_unavailable"
        if restricted:
            refs = [model_id for _, model_id in self._model_refs(rule.conditions)]
            if refs and self.c.items.nsfw_model_ids(refs):
                return "model_restricted"
        return None

    def _pause(self, rule: Rule, reason: str) -> None:
        if not self.c.rules.pause(rule.user_id, rule.id, reason):
            return
        rule.paused_reason = reason
        message = PAUSE_MESSAGES.get(reason, "\"{name}\" was paused.").format(name=rule.name, limit=self.hourly_limit())
        self._notify(
            user_id=rule.user_id, level="warning", title="Auto-organize paused a rule", message=message,
            type=NOTICE_RULE_PAUSED, metadata={"rule_id": rule.id, "reason": reason},
        )

    def _notify(self, **kwargs: Any) -> None:
        if self.c.notify is None:
            return
        try:
            self.c.notify(category="organize", **kwargs)
        except Exception:
            logger.warning("Auto-organize notification failed", exc_info=True)

    def load_items(self, subject: str, user_id: str, ids: Sequence[str], is_admin: bool,
                   restricted: bool) -> List[OrganizeItem]:
        if subject == "generation":
            if restricted:
                allowed = self.c.visibility.viewable_generation_ids(user_id, ids)
                ids = [i for i in ids if i in allowed]
            rows = self.c.items.generations(user_id, ids)
            rows = [r for r in rows if r["status"] == "completed"]
        elif subject == "upload":
            rows = self.c.items.uploads(user_id, ids)
        else:
            rows = self.c.items.models(user_id, ids, is_admin, restricted)
        return [OrganizeItem(subject=subject, item_id=r["id"], user_id=user_id, data=r) for r in rows]

    def _run_actions(self, rule: Rule, item: OrganizeItem, bump: bool) -> Tuple[List[Tuple[str, OrganizeChange]], Optional[str]]:
        applied: List[Tuple[str, OrganizeChange]] = []
        config_changed = False
        stored_actions = copy.deepcopy(rule.actions)
        blocked = None
        for action in stored_actions:
            definition = self.c.registry.action(action.get("action", ""))
            if definition is None:
                continue
            config = copy.deepcopy(action.get("config") or {})
            try:
                if definition.source == "core":
                    changes = definition.apply(item, config, rule.user_id, bump=bump)
                else:
                    changes = definition.apply(item, config, rule.user_id)
            except OrganizeActionBlocked as exc:
                blocked = exc.code
                break
            except Exception:
                logger.warning("Auto-organize action '%s' failed for %s", definition.key, item.item_id, exc_info=True)
                continue
            if definition.source == "core" and config != action.get("config"):
                action["config"] = config
                config_changed = True
            applied.extend((definition.key, change) for change in (changes or []) if isinstance(change, OrganizeChange))
        if config_changed:
            self.c.rules.store_actions(rule.user_id, rule.id, stored_actions)
            rule.actions = stored_actions
        return applied, blocked

    def _apply(self, rule: Rule, item: OrganizeItem, run_for: Callable[[], Run], bump: bool) -> Optional[int]:
        applied, blocked = self._run_actions(rule, item, bump)
        if applied:
            run = run_for()
            for action_key, change in applied:
                self.c.runs.record(
                    run, item.subject, item.item_id, action_key, change.target_type, change.target_id,
                    change.target_name, dict(change.data),
                )
        if blocked:
            self._pause(rule, blocked)
            return None
        run = run_for()
        self.c.runs.bump_run(rule.user_id, run.id, 1, 1 if applied else 0)
        self.c.rules.mark_handled(rule.id, item.subject, item.item_id)
        self.c.rules.touch_run(rule.user_id, rule.id)
        return len(applied)

    def process_item(self, subject: str, user_id: str, item_id: str, trigger: str = "item_created",
                     only_rules_older_than_item: bool = False) -> int:
        if self.is_paused_for(user_id):
            return 0
        rules = [r for r in self.c.rules.live_rules(user_id, subject) if not r.paused_reason]
        if not rules or (trigger != "item_created" and not any(self._listens_to(r, trigger) for r in rules)):
            return 0
        is_admin = self.c.rules.is_admin(user_id)
        restricted = self.c.visibility.is_restricted(user_id)
        items = self.load_items(subject, user_id, [item_id], is_admin, restricted)
        if not items:
            return 0
        item = items[0]
        item_created = dt_column(item.get("created_at"))
        filed = 0
        for rule in rules:
            if only_rules_older_than_item and item_created and rule.created_at and item_created < rule.created_at:
                continue
            if item.item_id in self.c.rules.handled_ids(rule.id, subject, [item.item_id]):
                if rule.stop_after:
                    break
                continue
            if trigger != "item_created" and not self._listens_to(rule, trigger):
                continue
            reason = self._blocking_reason(rule, user_id, is_admin, restricted)
            if reason:
                if reason == "model_restricted":
                    self._pause(rule, reason)
                continue
            if not evaluate(self.c.registry, rule.match, rule.conditions, item):
                continue
            if self.c.runs.live_items_last_hour(user_id, rule.id) >= self.hourly_limit():
                self._pause(rule, "rate_limited")
                continue
            result = self._apply(rule, item, self._live_run_factory(rule), bump=True)
            if result is None:
                continue
            filed += 1 if result else 0
            if rule.stop_after:
                break
        return filed

    def _live_run_factory(self, rule: Rule) -> Callable[[], Run]:
        holder: Dict[str, Run] = {}

        def get() -> Run:
            if "run" not in holder:
                holder["run"] = self.c.runs.open_live_run(rule.id, rule.name, rule.user_id, rule.subject)
            return holder["run"]

        return get

    def _listens_to(self, rule: Rule, trigger: str) -> bool:
        for condition in rule.conditions:
            fact = self.c.registry.fact(condition.get("fact", ""))
            if fact is not None and trigger in fact.triggers:
                return True
        return False

    def handle_event(self, kind: str, payload: Dict[str, Any]) -> None:
        if kind in ("generation_completed", "generation_tags_changed"):
            if kind == "generation_completed" and payload.get("status") != "completed":
                return
            generation_id = payload.get("generation_id")
            owner = self.c.items.generation_owner(generation_id) if generation_id else None
            if owner:
                trigger = "item_created" if kind == "generation_completed" else "tags_changed"
                self.process_item("generation", owner, generation_id, trigger)
        elif kind == "upload_created":
            upload_id = payload.get("upload_id")
            if payload.get("purpose", "user_upload") != "user_upload" or not upload_id:
                return
            owner = self.c.items.upload_owner(upload_id)
            if owner:
                self.process_item("upload", owner, upload_id)
        elif kind == "model_added":
            model_id = payload.get("model_id")
            if not model_id:
                return
            users = [payload["user_id"]] if payload.get("user_id") else self.c.rules.users_with_live_rules("model")
            for user_id in users:
                self.process_item("model", user_id, model_id)
        elif kind == "models_indexed":
            for user_id in self.c.rules.users_with_live_rules("model"):
                rules = self.c.rules.live_rules(user_id, "model")
                stamps = [r.created_at for r in rules if r.created_at]
                if not stamps:
                    continue
                for model_id in self.c.items.models_created_since(min(stamps).isoformat()):
                    self.process_item("model", user_id, model_id, only_rules_older_than_item=True)
        elif kind == "model_tags_changed":
            model_id = payload.get("model_id")
            if not model_id:
                return
            for user_id in self.c.rules.users_with_live_rules("model"):
                self.process_item("model", user_id, model_id, "tags_changed")
        elif kind == "model_metadata_changed":
            model_id = payload.get("model_id")
            if not model_id:
                return
            users = [payload["user_id"]] if payload.get("user_id") else self.c.rules.users_with_live_rules("model")
            for user_id in users:
                self.process_item("model", user_id, model_id, "metadata_changed")

    def _matching_ids(self, viewer: Viewer, subject: str, match: str, conditions: List[Dict[str, Any]],
                      cap: Optional[int]) -> Tuple[List[str], bool]:
        restricted = self.c.visibility.is_restricted(viewer.id)
        where, params, complete = sql_filter(self.c.registry, subject, match, conditions)
        if complete:
            ids = self.c.items.candidate_ids(subject, viewer.id, where, params, viewer.is_admin, restricted)
            if subject == "generation" and restricted:
                allowed = self.c.visibility.viewable_generation_ids(viewer.id, ids)
                ids = [i for i in ids if i in allowed]
            return ids, False
        limit = cap + 1 if cap else None
        candidates = self.c.items.candidate_ids(subject, viewer.id, where, params, viewer.is_admin, restricted, limit=limit)
        approximate = bool(cap) and len(candidates) > cap
        if cap:
            candidates = candidates[:cap]
        matched = []
        for start in range(0, len(candidates), BACKFILL_CHUNK):
            chunk = candidates[start:start + BACKFILL_CHUNK]
            for item in self.load_items(subject, viewer.id, chunk, viewer.is_admin, restricted):
                if evaluate(self.c.registry, match, conditions, item):
                    matched.append(item.item_id)
        return matched, approximate

    def preview(self, user: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        viewer = viewer_of(user)
        subject = payload.get("subject")
        shape, problems = validate_rule_shape(
            self.c.registry, subject, payload.get("match") or "all", payload.get("conditions") or [],
            payload.get("actions"), viewer.is_admin, require_actions=False,
            attribute_specs=self._attribute_specs_for(viewer),
        )
        if problems:
            raise errors.invalid_rule(problems)
        rule_id = payload.get("rule_id")
        if rule_id:
            self._require_rule(viewer.id, rule_id)
        ids, approximate = self._matching_ids(viewer, subject, shape["match"], shape["conditions"], PREVIEW_SCAN_CAP)
        handled = self.c.rules.handled_ids(rule_id, subject, ids) if rule_id else set()
        remaining = [i for i in ids if i not in handled]
        changing = self._would_change(viewer, subject, shape["actions"], remaining)
        duplicates = [
            {"rule_id": r.id, "name": r.name}
            for r in self.c.rules.list_for_user(viewer.id, subject)
            if r.id != rule_id and r.match == shape["match"]
            and canonical_conditions(r.conditions) == canonical_conditions(shape["conditions"])
        ]
        return {
            "matched": len(ids),
            "already_handled": len(handled),
            "would_change": len(changing),
            "approximate": approximate,
            "sample": [{"item_type": subject, "item_id": i} for i in ids[:SAMPLE_SIZE]],
            "duplicates": duplicates,
        }

    def _would_change(self, viewer: Viewer, subject: str, actions: List[Dict[str, Any]], ids: List[str]) -> Set[str]:
        remaining = set(ids)
        if not actions:
            return remaining
        changing: Set[str] = set()
        for action in actions:
            config = action.get("config") or {}
            if action["action"] == ADD_TO_COLLECTION:
                scope = COLLECTION_SCOPES[subject]
                collection_id = config.get("collection_id")
                if not collection_id or self.c.writer.find_collection(scope, collection_id, viewer.id) is None:
                    return remaining
                changing |= remaining - self.c.items.members_of(scope, collection_id, list(remaining))
            elif action["action"] == ADD_TAGS:
                changing |= remaining - self.c.items.items_with_all_tags(subject, list(remaining), config.get("tags") or [])
            else:
                return remaining
        return changing

    def start_backfill(self, user: Any, rule_id: str) -> Dict[str, Any]:
        viewer = viewer_of(user)
        rule = self._require_rule(viewer.id, rule_id)
        if self.is_paused_for(viewer.id):
            raise errors.organize_paused()
        running = self.jobs.active_for_rule(viewer.id, rule_id)
        if running is not None:
            raise errors.OrganizeError("job_running", "This rule is already being applied", 409, {"job_id": running.id})
        restricted = self.c.visibility.is_restricted(viewer.id)
        reason = self._blocking_reason(rule, viewer.id, viewer.is_admin, restricted)
        if reason:
            raise errors.OrganizeError("rule_not_runnable", "Fix what this rule needs before applying it", 422,
                                       {"reason": reason})
        job = self.jobs.create(viewer.id, rule.id, rule.name)
        self._executor.submit(self._run_backfill, job, viewer)
        return job.to_dict()

    def _run_backfill(self, job: BackfillJob, viewer: Viewer) -> None:
        try:
            self._backfill(job, viewer)
        except Exception:
            logger.exception("Auto-organize backfill %s failed", job.id)
            job.fail("Applying the rule stopped because of an error")
            if job.run_id:
                self.c.runs.finish_run(viewer.id, job.run_id, "failed")

    def _backfill(self, job: BackfillJob, viewer: Viewer) -> None:
        if job.status == "cancelled":
            return
        rule = self.c.rules.get(viewer.id, job.rule_id)
        if rule is None:
            job.fail("The rule was deleted")
            return
        job.start()
        restricted = self.c.visibility.is_restricted(viewer.id)
        ids, _approximate = self._matching_ids(viewer, rule.subject, rule.match, rule.conditions, None)
        handled = self.c.rules.handled_ids(rule.id, rule.subject, ids)
        ids = [i for i in ids if i not in handled]
        job.total = len(ids)
        run = self.c.runs.create_run(rule.id, rule.name, viewer.id, rule.subject, "backfill")
        job.run_id = run.id
        status = "completed"
        for start in range(0, len(ids), BACKFILL_CHUNK):
            if job.cancel_requested or self.is_paused_for(viewer.id):
                status = "cancelled"
                break
            chunk = ids[start:start + BACKFILL_CHUNK]
            blocked = False
            for item in self.load_items(rule.subject, viewer.id, chunk, viewer.is_admin, restricted):
                if not evaluate(self.c.registry, rule.match, rule.conditions, item):
                    continue
                result = self._apply(rule, item, lambda: run, bump=False)
                if result is None:
                    blocked = True
                    break
                if result:
                    job.applied += 1
            if rule.subject == "generation":
                self.c.writer.bump_history(viewer.id)
            job.processed = min(job.total, start + len(chunk))
            self._notify(user_id=viewer.id, level="info", title="Auto-organize", message=f"Applying \"{rule.name}\"",
                         type=NOTICE_JOB_PROGRESS, metadata=job.to_dict(), transient=True, show_toast=False)
            if blocked:
                status = "failed"
                job.error = "The collection this rule adds to no longer exists"
                break
        self.c.runs.finish_run(viewer.id, run.id, status)
        job.finish(status)
        if status != "failed":
            self._notify(user_id=viewer.id, level="success", title="Auto-organize",
                         message=self._finished_message(viewer.id, run.id, job.applied),
                         type=NOTICE_JOB_FINISHED, metadata=job.to_dict())

    def _finished_message(self, user_id: str, run_id: str, applied: int) -> str:
        if not applied:
            return "Nothing new to add"
        summary = self.c.runs.run_change_summary(user_id, [run_id]).get(run_id, [])
        targets = [row["target_name"] for row in summary if row["target_type"] in ("collection", "model_collection")]
        noun = "item" if applied == 1 else "items"
        if targets:
            return f"Added {applied} {noun} to {', '.join(dict.fromkeys(targets))}"
        return f"Organized {applied} {noun}"

    def list_jobs(self, user: Any, active: bool) -> List[Dict[str, Any]]:
        return [job.to_dict() for job in self.jobs.for_user(viewer_of(user).id, active)]

    def get_job(self, user: Any, job_id: str) -> Dict[str, Any]:
        job = self.jobs.get(viewer_of(user).id, job_id)
        if job is None:
            raise errors.OrganizeError("job_not_found", "Job not found", 404)
        return job.to_dict()

    def cancel_job(self, user: Any, job_id: str) -> Dict[str, Any]:
        job = self.jobs.get(viewer_of(user).id, job_id)
        if job is None:
            raise errors.OrganizeError("job_not_found", "Job not found", 404)
        job.request_cancel()
        return job.to_dict()

    def activity(self, user: Any, subject: Optional[str], rule_id: Optional[str], before: Optional[str],
                 limit: int) -> Dict[str, Any]:
        viewer = viewer_of(user)
        limit = max(1, min(limit, 200))
        runs = self.c.runs.list_runs(viewer.id, subject, rule_id, before, limit)
        run_ids = [r.id for r in runs]
        summary = self.c.runs.run_change_summary(viewer.id, run_ids)
        undone = self.c.runs.run_undone_items(viewer.id, run_ids)
        current_names = {r.id: r.name for r in self.c.rules.list_for_user(viewer.id)}
        scopes: Dict[str, Set[str]] = {}
        for run in runs:
            for row in summary.get(run.id, []):
                if row["target_type"] in ("collection", "model_collection"):
                    scopes.setdefault(COLLECTION_SCOPES[run.subject], set()).add(row["target_id"])
        names = {scope: self.c.writer.collection_names(scope, ids, viewer.id) for scope, ids in scopes.items()}
        entries = []
        for run in runs:
            collections, tags, other = [], [], []
            pending = 0
            for row in summary.get(run.id, []):
                live = row["total"] - row["undone"]
                pending += live
                if row["target_type"] in ("collection", "model_collection"):
                    current = names.get(COLLECTION_SCOPES[run.subject], {}).get(row["target_id"])
                    collections.append({"id": row["target_id"], "name": current or row["target_name"],
                                        "count": live, "created": bool(row["created"])})
                elif row["target_type"] == "tag":
                    tags.append({"id": row["target_id"], "name": row["target_name"], "count": live})
                else:
                    definition = self.c.registry.action(row["action_kind"])
                    other.append({"action": row["action_kind"],
                                  "label": definition.label if definition else row["action_kind"], "count": live})
            entry = run.base_dict()
            entry["rule_name"] = current_names.get(run.rule_id, run.rule_name)
            entry["undone"] = undone.get(run.id, 0)
            entry["can_undo"] = pending > 0 and not (run.status == "running" and run.kind == "backfill")
            entry["changes"] = {"collections": collections, "tags": tags, "other": other}
            entries.append(entry)
        return {"runs": entries, "next_before": runs[-1].id if len(runs) == limit else None}

    def undo_run(self, user: Any, run_id: str) -> Dict[str, Any]:
        viewer = viewer_of(user)
        run = self.c.runs.get_run(viewer.id, run_id)
        if run is None:
            raise errors.OrganizeError("run_not_found", "Nothing to undo", 404)
        if self.jobs.active_for_run(viewer.id, run_id) is not None:
            raise errors.OrganizeError("job_running", "Wait for the rule to finish before undoing", 409)
        undone, skipped, done_ids = 0, 0, []
        for application in self.c.runs.pending_applications(viewer.id, run_id):
            definition = self.c.registry.action(application.action_kind)
            item = OrganizeItem(subject=application.item_type, item_id=application.item_id, user_id=viewer.id)
            change = OrganizeChange(application.target_type, application.target_id, application.target_name,
                                    application.change)
            if definition is None or definition.undo is None:
                skipped += 1
                continue
            try:
                definition.undo(item, change, viewer.id)
                undone += 1
            except Exception:
                logger.warning("Auto-organize undo of %s failed", application.id, exc_info=True)
                skipped += 1
                continue
            done_ids.append(application.id)
        self.c.runs.mark_undone(viewer.id, done_ids)
        if not self.c.runs.pending_applications(viewer.id, run_id):
            self.c.runs.mark_run_undone(viewer.id, run_id)
        entries = self.activity(user, None, run.rule_id, None, 200)["runs"]
        current = next((e for e in entries if e["id"] == run_id), None)
        return {"undone": undone, "skipped": skipped, "run": current}

    def provenance(self, user: Any, item_type: str, item_id: str) -> List[Dict[str, Any]]:
        viewer = viewer_of(user)
        if item_type not in SUBJECT_TRIGGERS:
            raise errors.OrganizeError("bad_item_type", "Unknown item type", 400)
        rows = self.c.runs.provenance(viewer.id, item_type, item_id)
        scope = COLLECTION_SCOPES[item_type]
        names = self.c.writer.collection_names(
            scope, [r["target_id"] for r in rows if r["target_type"] in ("collection", "model_collection")], viewer.id
        )
        return [
            {
                "rule_id": r["rule_id"],
                "rule_name": r["rule_name"],
                "rule_deleted": bool(r["rule_deleted"]),
                "run_id": r["run_id"],
                "action": r["action_kind"],
                "target_type": r["target_type"] if r["target_type"] in ("collection", "model_collection", "tag") else "plugin",
                "target_id": r["target_id"],
                "target_name": names.get(r["target_id"], r["target_name"]),
                "created_at": dt_iso(r["created_at"]),
            }
            for r in rows
        ]

    def collection_rules(self, user: Any, scope: str, collection_id: str) -> List[Dict[str, Any]]:
        viewer = viewer_of(user)
        subject = next((s for s, sc in COLLECTION_SCOPES.items() if sc == scope), None)
        if subject is None:
            raise errors.OrganizeError("bad_scope", "Unknown collection scope", 400)
        rules = self.c.rules.rules_targeting_collection(viewer.id, subject, collection_id)
        return [{"id": r["id"], "name": r["name"], "status": r["status"]} for r in self.describe(viewer, rules)]

    def prune(self) -> int:
        return self.c.runs.prune()

    def admin_overview(self) -> Dict[str, Any]:
        controls = self.global_controls()
        counts = self.c.rules.rule_counts_by_user()
        user_controls = self.c.rules.all_user_controls()
        filed = self.c.runs.filed_totals_by_user()
        user_ids = list(dict.fromkeys(list(counts) + list(user_controls)))
        usernames = self.c.rules.usernames(user_ids)
        default_cap = self.default_cap()
        users = []
        for user_id in user_ids:
            if user_id not in usernames:
                continue
            users.append(self._admin_row(user_id, usernames[user_id], counts.get(user_id), user_controls.get(user_id),
                                         filed.get(user_id), default_cap))
        totals = {
            "users_with_rules": sum(1 for row in users if row["rules"]),
            "rules": sum(row["rules"] for row in users),
            "enabled_rules": sum(row["enabled_rules"] for row in users),
            "paused_rules": sum(row["paused_rules"] for row in users),
            "items_filed_24h": sum(row["items_filed_24h"] for row in users),
            "items_filed_total": sum(row["items_filed_total"] for row in users),
            "running_jobs": self.jobs.running_count(),
        }
        return {
            "paused_all": controls.paused,
            "default_rule_cap": default_cap,
            "hourly_limit": controls.hourly_limit or DEFAULT_HOURLY_LIMIT,
            "totals": totals,
            "users": users,
        }

    def _admin_row(self, user_id: str, username: str, counts: Optional[tuple], controls: Optional[Controls],
                   filed: Optional[Dict[str, int]], default_cap: int) -> Dict[str, Any]:
        total, enabled, paused, last_run_at = counts or (0, 0, 0, None)
        controls = controls or Controls()
        return {
            "user_id": user_id,
            "username": username,
            "rules": total,
            "enabled_rules": enabled,
            "paused_rules": paused,
            "items_filed_24h": (filed or {}).get("day", 0),
            "items_filed_total": (filed or {}).get("total", 0),
            "paused": controls.paused,
            "rule_cap": controls.rule_cap,
            "effective_rule_cap": default_cap if controls.rule_cap is None else controls.rule_cap,
            "last_run_at": dt_iso(last_run_at),
        }

    def admin_controls(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        controls = self.global_controls()
        if payload.get("paused_all") is not None:
            controls.paused = bool(payload["paused_all"])
        if payload.get("default_rule_cap") is not None:
            controls.rule_cap = int(payload["default_rule_cap"])
        if payload.get("hourly_limit") is not None:
            controls.hourly_limit = int(payload["hourly_limit"])
        self.c.rules.save_controls(GLOBAL_SCOPE, controls)
        if controls.paused:
            self.jobs.cancel_all()
        return self.admin_overview()

    def admin_user(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if user_id == GLOBAL_SCOPE or not self.c.rules.user_exists(user_id):
            raise errors.OrganizeError("user_not_found", "User not found", 404)
        controls = self.c.rules.controls(user_id)
        if payload.get("paused") is not None:
            controls.paused = bool(payload["paused"])
        if "rule_cap" in payload:
            controls.rule_cap = None if payload["rule_cap"] is None else int(payload["rule_cap"])
        self.c.rules.save_controls(user_id, controls)
        if controls.paused:
            self.jobs.cancel_for_user(user_id)
        row = next((u for u in self.admin_overview()["users"] if u["user_id"] == user_id), None)
        return row
