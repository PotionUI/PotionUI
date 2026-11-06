import copy
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Collection, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from src.features.generation.policy import GenerationPolicy
from src.features.organize import errors
from src.features.organize.core_actions import (
    ADD_MODEL_TAGS,
    ADD_TAGS,
    ADD_TO_COLLECTION,
    ASSIGN_MODEL_GROUP,
    SET_MODEL_TYPE,
    resolve_collection,
)
from src.features.organize.evaluator import as_list, canonical_conditions, evaluate, sql_filter
from src.features.organize.item_repository import OrganizeItemRepository
from src.features.organize.jobs import JobBook
from src.features.organize.outcome_repository import OrganizeOutcomeRepository
from src.features.organize.records import COLLECTION_SCOPES, SUBJECT_TRIGGERS, Controls, Outcome, Rule, Run
from src.features.organize.rule_repository import GLOBAL_SCOPE, INSTANCE_SCOPE, OrganizeRuleRepository, RuleCapReached
from src.features.organize.run_repository import OrganizeRunRepository
from src.features.organize.templates import list_templates
from src.features.organize.validation import problem, validate_name, validate_rule_shape
from src.features.organize.visibility import OrganizeVisibility
from src.features.organize.write_repository import OrganizeWriteRepository
from src.features.users.repository import UserRepository
from src.platform.database.rows import dt_column, dt_iso, now_utc
from src.platform.plugins.organize import (
    FACT_KINDS,
    OPERATOR_LABELS,
    OrganizeActionBlocked,
    OrganizeActionRetry,
    OrganizeActionSkipped,
    OrganizeChange,
    OrganizeItem,
    OrganizeRegistry,
)

logger = logging.getLogger(__name__)

DEFAULT_RULE_CAP = 50
DEFAULT_INSTANCE_RULE_CAP = 100
MAX_ATTEMPTS = 5
RETRY_BACKOFF_MINUTES = (5, 15, 60, 240)
SWEEP_PER_RULE = 25
SWEEP_TOTAL = 200
PACED_CHUNK = 25
UNVERIFIED = "unverified"
DEFAULT_HOURLY_LIMIT = 200
BACKFILL_CHUNK = 500
PREVIEW_SCAN_CAP = 5000
GENERATION_EVENT_TRIGGERS = {
    "generation_completed": "item_created",
    "generation_tags_changed": "tags_changed",
    "generation_detected_tags_changed": "detected_tags_changed",
}
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
    "group_missing": "\"{name}\" was paused because the user group it gives access to no longer exists.",
    "provider_unavailable": "\"{name}\" was paused because the provider it uses is not set up.",
}

BLOCKING_CODES = {
    "fact_unavailable", "action_unavailable", "collection_missing", "model_restricted", "group_missing",
}
PAUSING_CODES = ("model_restricted", "group_missing")


@dataclass
class OrganizeCollaborators:
    rules: OrganizeRuleRepository
    runs: OrganizeRunRepository
    items: OrganizeItemRepository
    writer: OrganizeWriteRepository
    users: UserRepository
    registry: OrganizeRegistry
    visibility: OrganizeVisibility
    notify: Optional[Callable[..., Any]] = None
    outcomes: OrganizeOutcomeRepository = field(default_factory=OrganizeOutcomeRepository)
    ops: Optional[Any] = None


@dataclass
class Viewer:
    id: Optional[str]
    is_admin: bool
    actor: Optional[str] = None

    @property
    def scope(self) -> str:
        return "instance" if self.id is None else "user"


@dataclass
class ApplyResult:
    changed: int = 0
    skipped: int = 0
    waiting: bool = False
    blocked: Optional[str] = None


@dataclass
class RetryTotals:
    processed: int = 0
    changed: int = 0
    skipped: int = 0
    waiting: int = 0
    blocked: Optional[str] = None


def viewer_of(user: Any) -> Viewer:
    if isinstance(user, Viewer):
        return user
    account_type = getattr(user, "account_type", None)
    value = getattr(account_type, "value", account_type)
    return Viewer(id=user.id, is_admin=str(value).upper() == "ADMIN", actor=user.id)


def instance_viewer(admin: Any) -> Viewer:
    return Viewer(id=None, is_admin=True, actor=getattr(admin, "id", None))


class OrganizeManager:

    def __init__(self, collaborators: OrganizeCollaborators, executor: Optional[Any] = None):
        self.c = collaborators
        self.jobs = JobBook()
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="organize-backfill")
        self._options_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="organize-options")

    def shutdown(self) -> None:
        self.jobs.cancel_all()
        shutdown = getattr(self.executor, "shutdown", None)
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

    def effective_cap(self, user_id: Optional[str]) -> int:
        if user_id is None:
            instance_cap = self.c.rules.controls(INSTANCE_SCOPE).rule_cap
            return DEFAULT_INSTANCE_RULE_CAP if instance_cap is None else instance_cap
        user_cap = self.c.rules.controls(user_id).rule_cap
        return self.default_cap() if user_cap is None else user_cap

    def is_paused_for(self, user_id: Optional[str]) -> bool:
        return self.global_controls().paused or self.c.rules.controls(user_id or INSTANCE_SCOPE).paused

    def _restricted(self, user_id: Optional[str]) -> bool:
        return False if user_id is None else bool(self.c.visibility.is_restricted(user_id))

    def _is_admin_of(self, user_id: Optional[str]) -> bool:
        return True if user_id is None else GenerationPolicy.is_admin(self.c.users.get_by_id(user_id))

    def _recipient(self, rule: Rule) -> Optional[str]:
        return rule.user_id or rule.updated_by or rule.created_by

    def _group_names(self, group_ids: Iterable[str]) -> Dict[str, str]:
        ids = [g for g in dict.fromkeys(group_ids) if g]
        if not ids or self.c.ops is None:
            return {}
        return self.c.ops.group_names(ids)

    def catalog(self, user: Any, subject: Optional[str] = None) -> Dict[str, Any]:
        viewer = viewer_of(user)
        if viewer.id is None:
            subject = "model"
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
                "triggers": list(f.triggers),
                "ignore_separators": f.ignore_separators,
            }
            for f in self.c.registry.facts(subject)
        ]
        actions = [
            {
                "key": a.key,
                "label": a.label,
                "subjects": list(a.subjects),
                "config_schema": self._catalog_schema(a),
                "requires_admin": a.requires_admin,
                "undoable": a.undo is not None,
                "description": a.description,
                "source": a.source,
                "component": a.component,
                "scope": viewer.scope,
                "notes": list(a.notes),
                "needs": list(a.needs),
                "done_label": a.done_label,
                "paced": a.paced,
            }
            for a in self.c.registry.actions(subject)
            if viewer.scope in a.scopes and (viewer.is_admin or not a.requires_admin)
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
            if viewer.id is not None or key == "model"
        ]
        return {
            "scope": viewer.scope,
            "subjects": subjects,
            "kinds": {kind: {"operators": list(ops)} for kind, ops in FACT_KINDS.items()},
            "operators": dict(OPERATOR_LABELS),
            "facts": facts,
            "actions": actions,
        }

    def _catalog_schema(self, action: Any) -> List[Dict[str, Any]]:
        schema = [dict(entry) for entry in action.config_schema]
        if action.key == SET_MODEL_TYPE and self.c.ops is not None:
            options = [{"value": t, "label": t} for t in self.c.ops.assignable_types()]
            for entry in schema:
                if entry.get("key") == "model_type":
                    entry["options"] = options
        return schema

    def fact_options(self, user: Any, key: str, subject: Optional[str], query: str, limit: int) -> List[Dict[str, str]]:
        viewer = viewer_of(user)
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
        return self._handler_options(fact, viewer.id, subject, query)[:limit]

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

    def templates(self, subject: Optional[str], scope: str = "user") -> List[Dict[str, Any]]:
        available = []
        for template in list_templates(subject, scope):
            keys = [a.get("action") for a in template["rule"].get("actions", [])]
            if all(self.c.registry.action(key) is not None for key in keys):
                available.append(template)
        return copy.deepcopy(available)

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
        return self.describe(viewer, [self.require_rule(viewer.id, rule_id)])[0]

    def require_rule(self, user_id: str, rule_id: str) -> Rule:
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
            created_by=viewer.actor, updated_by=viewer.actor,
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
        rule = self.require_rule(viewer.id, rule_id)
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
        rule.updated_by = viewer.actor
        updated = self.c.rules.update(rule, reset_handled=reset)
        if updated is None:
            raise errors.rule_not_found()
        return self.describe(viewer, [updated])[0]

    def patch_rule(self, user: Any, rule_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        viewer = viewer_of(user)
        rule = self.require_rule(viewer.id, rule_id)
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
        rule.updated_by = viewer.actor
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
        rule = self.require_rule(viewer.id, rule_id)
        cap = self.effective_cap(viewer.id)
        copy_rule = Rule(
            id="", user_id=viewer.id, name=f"{rule.name} (copy)"[:120], subject=rule.subject, trigger=rule.trigger,
            match=rule.match, conditions=copy.deepcopy(rule.conditions), actions=copy.deepcopy(rule.actions),
            enabled=False, stop_after=rule.stop_after, created_by=viewer.actor, updated_by=viewer.actor,
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
            scope=viewer.scope,
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
        restricted = self._restricted(viewer.id)
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
            if action["action"] == ASSIGN_MODEL_GROUP and self.c.ops is not None:
                if self.c.ops.group_name(action["config"].get("group_id")) is None:
                    problems.append(problem(
                        f"actions.{index}.config.group_id", "group_not_found", "That user group does not exist"
                    ))
                continue
            if action["action"] == SET_MODEL_TYPE and self.c.ops is not None:
                if action["config"].get("model_type") not in self.c.ops.assignable_types():
                    problems.append(problem(
                        f"actions.{index}.config.model_type", "bad_config", "A model cannot be set to that type"
                    ))
                continue
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
        restricted = self._restricted(viewer.id)
        collection_ids: Dict[str, Set[str]] = {}
        group_ids: Set[str] = set()
        model_ids: Set[str] = set()
        for rule in rules:
            for action in rule.actions:
                if action.get("action") == ADD_TO_COLLECTION and action.get("config", {}).get("collection_id"):
                    collection_ids.setdefault(COLLECTION_SCOPES[rule.subject], set()).add(action["config"]["collection_id"])
                if action.get("action") == ASSIGN_MODEL_GROUP and action.get("config", {}).get("group_id"):
                    group_ids.add(action["config"]["group_id"])
            model_ids.update(model_id for _, model_id in self._model_refs(rule.conditions))
        names = {
            scope: self.c.writer.collection_names(scope, ids, viewer.id) for scope, ids in collection_ids.items()
        }
        group_names = self._group_names(group_ids)
        outcome_counts = self.c.outcomes.counts_by_rule([r.id for r in rules])
        actors = self.c.rules.usernames(
            [a for r in rules for a in (r.created_by, r.updated_by) if a]
        )
        existing_models = self.c.items.existing_model_ids(model_ids) if model_ids else set()
        nsfw = self.c.items.nsfw_model_ids(model_ids) if model_ids and restricted else set()
        model_names = self.c.items.model_names(model_ids) if model_ids else {}
        filed = self.c.runs.filed_counts(viewer.id, [r.id for r in rules])
        described = []
        for rule in rules:
            issues = self._issues(
                viewer, rule, names.get(COLLECTION_SCOPES[rule.subject], {}), existing_models, nsfw, model_names, group_names
            )
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
            groups = [
                {
                    "id": action["config"].get("group_id"),
                    "name": group_names.get(action["config"].get("group_id"), ""),
                    "exists": action["config"].get("group_id") in group_names,
                }
                for action in rule.actions
                if action.get("action") == ASSIGN_MODEL_GROUP
            ]
            counts = outcome_counts.get(rule.id, {})
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
                "targets": {"collections": targets, "groups": groups},
                "filed_count": filed.get(rule.id, 0),
                "waiting_count": counts.get("waiting", 0),
                "skipped_count": counts.get("skipped", 0),
                "last_run_at": dt_iso(rule.last_run_at),
                "created_at": dt_iso(rule.created_at),
                "updated_at": dt_iso(rule.updated_at),
                "scope": viewer.scope,
                "created_by": rule.created_by,
                "created_by_name": actors.get(rule.created_by),
                "updated_by": rule.updated_by,
                "updated_by_name": actors.get(rule.updated_by),
            })
        return described

    def _issues(self, viewer: Viewer, rule: Rule, collection_names: Dict[str, str], existing_models: Set[str],
                nsfw: Set[str], model_names: Dict[str, str], group_names: Dict[str, str]) -> List[Dict[str, Any]]:
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
            if definition is None or (definition.requires_admin and not viewer.is_admin) or viewer.scope not in definition.scopes:
                issues.append({"code": "action_unavailable", "message": "Something this rule does is no longer available",
                               "blocking": True, "path": path})
                continue
            if definition.key == ASSIGN_MODEL_GROUP and action.get("config", {}).get("group_id") not in group_names:
                issues.append({"code": "group_missing", "message": "The user group this rule gives access to no longer exists",
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

    def blocking_reason(self, rule: Rule, user_id: Optional[str], is_admin: bool, restricted: bool) -> Optional[str]:
        scope = "instance" if rule.user_id is None else "user"
        for condition in rule.conditions:
            if self.c.registry.fact(condition.get("fact", "")) is None:
                return "fact_unavailable"
        for action in rule.actions:
            definition = self.c.registry.action(action.get("action", ""))
            if definition is None or (definition.requires_admin and not is_admin) or scope not in definition.scopes:
                return "action_unavailable"
            if definition.key == ASSIGN_MODEL_GROUP and self.c.ops is not None:
                if self.c.ops.group_name(action.get("config", {}).get("group_id")) is None:
                    return "group_missing"
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
        self.notify(
            user_id=self._recipient(rule), level="warning", title="Auto-organize paused a rule", message=message,
            type=NOTICE_RULE_PAUSED, metadata={"rule_id": rule.id, "reason": reason},
        )

    def notify(self, **kwargs: Any) -> None:
        if self.c.notify is None or kwargs.get("user_id") is None:
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

    def _rule_needs_digest(self, rule: Rule) -> bool:
        for action in rule.actions:
            definition = self.c.registry.action(action.get("action", ""))
            if definition is not None and "digest" in definition.needs:
                return True
        return False

    def rule_is_paced(self, rule: Rule) -> bool:
        for action in rule.actions:
            definition = self.c.registry.action(action.get("action", ""))
            if definition is not None and definition.paced:
                return True
        return False

    def _run_actions(self, rule: Rule, item: OrganizeItem, bump: bool):
        applied: List[Tuple[str, OrganizeChange]] = []
        skips: List[Tuple[str, str, str]] = []
        retry: Optional[Tuple[str, str, str, Optional[float]]] = None
        config_changed = False
        stored_actions = copy.deepcopy(rule.actions)
        blocked = None
        for action in stored_actions:
            definition = self.c.registry.action(action.get("action", ""))
            if definition is None:
                continue
            if "digest" in definition.needs and item.subject == "model" and not item.get("sha256"):
                retry = (definition.key, UNVERIFIED, "The file is not verified yet", None)
                break
            config = copy.deepcopy(action.get("config") or {})
            try:
                if definition.source == "core":
                    changes = definition.apply(item, config, rule.user_id, bump=bump)
                else:
                    changes = definition.apply(item, config, rule.user_id)
            except OrganizeActionBlocked as exc:
                blocked = exc.code
                break
            except OrganizeActionSkipped as exc:
                skips.append((definition.key, exc.code, exc.message))
                continue
            except OrganizeActionRetry as exc:
                retry = (definition.key, exc.code, exc.message, exc.after_s)
                break
            except Exception:
                logger.warning("Auto-organize action '%s' failed for %s", definition.key, item.item_id, exc_info=True)
                retry = (definition.key, "error", "The action failed and will be tried again", None)
                break
            if definition.source == "core" and config != action.get("config"):
                action["config"] = config
                config_changed = True
            applied.extend((definition.key, change) for change in (changes or []) if isinstance(change, OrganizeChange))
        if config_changed:
            self.c.rules.store_actions(rule.user_id, rule.id, stored_actions)
            rule.actions = stored_actions
        return applied, skips, retry, blocked

    def apply(self, rule: Rule, item: OrganizeItem, run_for: Callable[[], Run], bump: bool) -> ApplyResult:
        applied, skips, retry, blocked = self._run_actions(rule, item, bump)
        if applied:
            run = run_for()
            for action_key, change in applied:
                self.c.runs.record(
                    run, item.subject, item.item_id, action_key, change.target_type, change.target_id,
                    change.target_name, dict(change.data),
                )
        if blocked:
            self._pause(rule, blocked)
            return ApplyResult(changed=len(applied), blocked=blocked)
        run = run_for()
        previous = self.c.outcomes.get(rule.id, item.subject, item.item_id)
        counted = 0 if previous is not None else 1
        if retry is not None:
            action_key, code, message, after_s = retry
            attempts = (previous.attempts if previous else 0) + (0 if code == UNVERIFIED else 1)
            if attempts >= MAX_ATTEMPTS:
                skips.append((action_key, "gave_up", f"Gave up after {MAX_ATTEMPTS} tries: {message}"))
                retry = None
            else:
                next_try = None
                if code != UNVERIFIED:
                    delay = timedelta(seconds=after_s) if after_s else timedelta(
                        minutes=RETRY_BACKOFF_MINUTES[min(attempts, len(RETRY_BACKOFF_MINUTES)) - 1]
                    )
                    next_try = now_utc() + delay
                self.c.outcomes.upsert(Outcome(
                    rule_id=rule.id, item_type=item.subject, item_id=item.item_id, outcome="waiting", code=code,
                    detail=message, run_id=run.id, attempts=attempts, next_try_at=next_try,
                ))
                self.c.runs.bump_run(rule.user_id, run.id, counted, 1 if applied else 0)
                return ApplyResult(changed=len(applied), skipped=len(skips), waiting=True)
        if skips:
            _key, code, message = skips[0]
            self.c.outcomes.upsert(Outcome(
                rule_id=rule.id, item_type=item.subject, item_id=item.item_id, outcome="skipped", code=code,
                detail=message, run_id=run.id, attempts=previous.attempts if previous else 0,
            ))
        elif previous is not None:
            self.c.outcomes.clear(rule.id, item.subject, item.item_id)
        self.c.runs.bump_run(rule.user_id, run.id, counted, 1 if applied else 0)
        self.c.rules.mark_handled(rule.id, item.subject, item.item_id)
        self.c.rules.touch_run(rule.user_id, rule.id)
        return ApplyResult(changed=len(applied), skipped=len(skips))

    def process_item(self, subject: str, user_id: Optional[str], item_id: str, trigger: str = "item_created",
                     only_rules_older_than_item: bool = False, force_rules: Collection[str] = ()) -> int:
        if self.is_paused_for(user_id):
            return 0
        rules = [r for r in self.c.rules.live_rules(user_id, subject) if not r.paused_reason]
        if trigger == "item_created":
            rules = [r for r in rules if not self._awaits_event(r)]
        elif trigger == "model_verified":
            rules = [r for r in rules if r.id in force_rules or self._rule_needs_digest(r)]
        generic = trigger in ("item_created", "model_verified")
        if not rules or (not generic and not any(self._listens_to(r, trigger) for r in rules)):
            return 0
        is_admin = self._is_admin_of(user_id)
        restricted = self._restricted(user_id)
        items = self.load_items(subject, user_id, [item_id], is_admin, restricted)
        if not items:
            return 0
        item = items[0]
        item_created = dt_column(item.get("created_at"))
        filed = 0
        for rule in rules:
            if only_rules_older_than_item and rule.id not in force_rules and item_created and rule.created_at \
                    and item_created < rule.created_at:
                continue
            if item.item_id in self.c.rules.handled_ids(rule.id, subject, [item.item_id]):
                if rule.stop_after:
                    break
                continue
            if not generic and not self._listens_to(rule, trigger):
                continue
            reason = self.blocking_reason(rule, user_id, is_admin, restricted)
            if reason:
                if reason in PAUSING_CODES:
                    self._pause(rule, reason)
                continue
            if not evaluate(self.c.registry, rule.match, rule.conditions, item):
                continue
            if not self.rule_is_paced(rule) and self.c.runs.live_items_last_hour(user_id, rule.id) >= self.hourly_limit():
                self._pause(rule, "rate_limited")
                continue
            result = self.apply(rule, item, self._live_run_factory(rule), bump=True)
            if result.blocked:
                continue
            filed += 1 if result.changed else 0
            if rule.stop_after and not result.waiting:
                break
        return filed

    def _live_run_factory(self, rule: Rule) -> Callable[[], Run]:
        holder: Dict[str, Run] = {}

        def get() -> Run:
            if "run" not in holder:
                holder["run"] = self.c.runs.open_live_run(rule.id, rule.name, rule.user_id, rule.subject)
            return holder["run"]

        return get

    def _awaits_event(self, rule: Rule) -> bool:
        for condition in rule.conditions:
            fact = self.c.registry.fact(condition.get("fact", ""))
            if fact is not None and "item_created" not in fact.triggers:
                return True
        return False

    def _listens_to(self, rule: Rule, trigger: str) -> bool:
        for condition in rule.conditions:
            fact = self.c.registry.fact(condition.get("fact", ""))
            if fact is not None and trigger in fact.triggers:
                return True
        return False

    def _model_owners(self, include_instance: bool = True) -> List[Optional[str]]:
        owners: List[Optional[str]] = list(self.c.rules.users_with_live_rules("model"))
        if include_instance:
            owners.append(None)
        return owners

    def handle_event(self, kind: str, payload: Dict[str, Any]) -> None:
        if kind in ("generation_completed", "generation_tags_changed", "generation_detected_tags_changed"):
            if kind == "generation_completed" and payload.get("status") != "completed":
                return
            generation_id = payload.get("generation_id")
            owner = self.c.items.generation_owner(generation_id) if generation_id else None
            if owner:
                trigger = GENERATION_EVENT_TRIGGERS[kind]
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
            owners = [payload["user_id"]] if payload.get("user_id") else self._model_owners()
            for owner in owners:
                self.process_item("model", owner, model_id)
        elif kind == "models_indexed":
            for owner in self._model_owners():
                rules = self.c.rules.live_rules(owner, "model")
                stamps = [r.created_at for r in rules if r.created_at]
                if not stamps:
                    continue
                for model_id in self.c.items.models_created_since(dt_iso(min(stamps))):
                    self.process_item("model", owner, model_id, only_rules_older_than_item=True)
        elif kind == "models_verified":
            model_ids = [m for m in payload.get("model_ids") or [] if m]
            self.handle_event("models_indexed", {})
            forced: Dict[str, Set[str]] = {}
            for waiting in self.c.outcomes.waiting_for_items("model", model_ids):
                forced.setdefault(waiting.item_id, set()).add(waiting.rule_id)
            owners = self._model_owners()
            for model_id in model_ids:
                for owner in owners:
                    self.process_item(
                        "model", owner, model_id, "model_verified", only_rules_older_than_item=True,
                        force_rules=forced.get(model_id, ()),
                    )
        elif kind == "model_tags_changed":
            model_id = payload.get("model_id")
            if not model_id:
                return
            for owner in self._model_owners():
                self.process_item("model", owner, model_id, "tags_changed")
        elif kind == "model_metadata_changed":
            model_id = payload.get("model_id")
            if not model_id:
                return
            owners = [payload["user_id"]] if payload.get("user_id") else self._model_owners()
            for owner in owners:
                self.process_item("model", owner, model_id, "metadata_changed")

    def retry_items(self, rule: Rule, item_ids: Sequence[str], run_for: Callable[[], Run],
                    progress: Optional[Callable[[RetryTotals], None]] = None,
                    cancelled: Optional[Callable[[], bool]] = None) -> RetryTotals:
        totals = RetryTotals()
        is_admin = self._is_admin_of(rule.user_id)
        restricted = self._restricted(rule.user_id)
        loaded = {i.item_id: i for i in self.load_items(rule.subject, rule.user_id, list(item_ids), is_admin, restricted)}
        handled = self.c.rules.handled_ids(rule.id, rule.subject, item_ids)
        for item_id in item_ids:
            if cancelled is not None and cancelled():
                break
            item = loaded.get(item_id)
            totals.processed += 1
            if item is None or item_id in handled or not evaluate(self.c.registry, rule.match, rule.conditions, item):
                self.c.outcomes.clear(rule.id, rule.subject, item_id)
            else:
                result = self.apply(rule, item, run_for, bump=False)
                if result.blocked:
                    totals.blocked = result.blocked
                    break
                totals.changed += 1 if result.changed else 0
                totals.skipped += result.skipped
                totals.waiting += 1 if result.waiting else 0
            if progress is not None:
                progress(totals)
        return totals

    def retry_waiting(self, limit: int = SWEEP_TOTAL, per_rule: int = SWEEP_PER_RULE) -> int:
        due = self.c.outcomes.due(now_utc(), limit)
        by_rule: Dict[str, List[str]] = {}
        for outcome in due:
            bucket = by_rule.setdefault(outcome.rule_id, [])
            if len(bucket) < per_rule:
                bucket.append(outcome.item_id)
        processed = 0
        for rule_id, item_ids in by_rule.items():
            rule = self.c.rules.get_by_id(rule_id)
            if rule is None or not rule.enabled or rule.paused_reason or self.is_paused_for(rule.user_id):
                continue
            if self.blocking_reason(rule, rule.user_id, self._is_admin_of(rule.user_id), self._restricted(rule.user_id)):
                continue
            totals = self.retry_items(rule, item_ids, self._live_run_factory(rule))
            processed += totals.processed
        return processed

    def run_items(self, user: Any, run_id: str, status: Optional[str], page: int, page_size: int) -> Dict[str, Any]:
        viewer = viewer_of(user)
        run = self.c.runs.get_run(viewer.id, run_id)
        if run is None:
            raise errors.OrganizeError("run_not_found", "Run not found", 404)
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        wanted = status if status in ("updated", "skipped", "waiting") else None
        found = self.c.outcomes.run_items(viewer.id, run_id, wanted, page_size, (page - 1) * page_size)
        labels = self.c.items.model_labels([r["item_id"] for r in found["rows"]]) if run.subject == "model" else {}
        items = []
        for row in found["rows"]:
            label = labels.get(row["item_id"], {})
            items.append({
                "item_type": row["item_type"],
                "item_id": row["item_id"],
                "name": label.get("name") or row["item_id"],
                "model_type": label.get("model_type"),
                "status": row["status"],
                "code": row["code"] if row["status"] != "updated" else None,
                "detail": row["detail"] if row["status"] != "updated" else (row["detail"] or ""),
                "attempts": row["attempts"],
                "next_try_at": row["next_try_at"],
            })
        return {"run": run.base_dict(), "items": items, "total": found["total"], "page": page, "page_size": page_size}

    def matching_ids(self, viewer: Viewer, subject: str, match: str, conditions: List[Dict[str, Any]],
                      cap: Optional[int]) -> Tuple[List[str], bool]:
        restricted = self._restricted(viewer.id)
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

    def _preview_shape(self, viewer: Viewer, payload: Dict[str, Any]) -> Dict[str, Any]:
        shape, problems = validate_rule_shape(
            self.c.registry, payload.get("subject"), payload.get("match") or "all", payload.get("conditions") or [],
            payload.get("actions"), viewer.is_admin, require_actions=False,
            attribute_specs=self._attribute_specs_for(viewer), scope=viewer.scope,
        )
        if problems:
            raise errors.invalid_rule(problems)
        rule_id = payload.get("rule_id")
        if rule_id:
            self.require_rule(viewer.id, rule_id)
        return shape

    def _classify(self, viewer: Viewer, shape: Dict[str, Any], rule_id: Optional[str]) -> Dict[str, Any]:
        subject = shape["subject"]
        ids, approximate = self.matching_ids(viewer, subject, shape["match"], shape["conditions"], PREVIEW_SCAN_CAP)
        handled = self.c.rules.handled_ids(rule_id, subject, ids) if rule_id else set()
        remaining = [i for i in ids if i not in handled]
        changing = self._would_change(viewer, subject, shape["actions"], remaining)
        return {"ids": ids, "approximate": approximate, "handled": handled, "changing": changing}

    def preview(self, user: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        viewer = viewer_of(user)
        shape = self._preview_shape(viewer, payload)
        subject = shape["subject"]
        rule_id = payload.get("rule_id")
        found = self._classify(viewer, shape, rule_id)
        ids = found["ids"]
        duplicates = [
            {"rule_id": r.id, "name": r.name}
            for r in self.c.rules.list_for_user(viewer.id, subject)
            if r.id != rule_id and r.match == shape["match"]
            and canonical_conditions(r.conditions) == canonical_conditions(shape["conditions"])
        ]
        labels = self.c.items.model_labels(ids[:SAMPLE_SIZE]) if subject == "model" else {}
        sample = []
        for item_id in ids[:SAMPLE_SIZE]:
            entry = {"item_type": subject, "item_id": item_id}
            if item_id in labels:
                entry["name"] = labels[item_id]["name"]
                entry["model_type"] = labels[item_id]["model_type"]
            sample.append(entry)
        return {
            "matched": len(ids),
            "already_handled": len(found["handled"]),
            "would_change": len(found["changing"]),
            "approximate": found["approximate"],
            "sample": sample,
            "duplicates": duplicates,
        }

    def preview_matches(self, user: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        viewer = viewer_of(user)
        shape = self._preview_shape(viewer, payload)
        subject = shape["subject"]
        found = self._classify(viewer, shape, payload.get("rule_id"))
        changing = found["changing"]
        statuses = {i: ("would_change" if i in changing else "already_done") for i in found["ids"]}
        wanted = payload.get("status")
        ids = [i for i in found["ids"] if wanted not in ("would_change", "already_done") or statuses[i] == wanted]
        page = max(1, int(payload.get("page") or 1))
        page_size = max(1, min(int(payload.get("page_size") or 25), 100))
        window = ids[(page - 1) * page_size:page * page_size]
        labels = self.c.items.model_labels(window) if subject == "model" else {}
        return {
            "total": len(ids),
            "page": page,
            "page_size": page_size,
            "approximate": found["approximate"],
            "counts": {
                "would_change": sum(1 for v in statuses.values() if v == "would_change"),
                "already_done": sum(1 for v in statuses.values() if v == "already_done"),
            },
            "items": [
                {
                    "item_type": subject,
                    "item_id": i,
                    "name": labels.get(i, {}).get("name") or i,
                    "model_type": labels.get(i, {}).get("model_type"),
                    "status": statuses[i],
                }
                for i in window
            ],
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
            elif action["action"] == ASSIGN_MODEL_GROUP:
                group_id = config.get("group_id")
                if not group_id:
                    return remaining
                changing |= remaining - self.c.items.members_of("user_group", group_id, list(remaining))
            elif action["action"] == ADD_MODEL_TAGS:
                changing |= remaining - self.c.items.items_with_all_tags("model", list(remaining), config.get("tags") or [])
            elif action["action"] == SET_MODEL_TYPE:
                if not config.get("model_type"):
                    return remaining
                changing |= remaining - self.c.items.models_of_type(list(remaining), config["model_type"])
            else:
                return remaining
        return changing

    def activity(self, user: Any, subject: Optional[str], rule_id: Optional[str], before: Optional[str],
                 limit: int) -> Dict[str, Any]:
        viewer = viewer_of(user)
        limit = max(1, min(limit, 200))
        runs = self.c.runs.list_runs(viewer.id, subject, rule_id, before, limit)
        run_ids = [r.id for r in runs]
        summary = self.c.runs.run_change_summary(viewer.id, run_ids)
        undone = self.c.runs.run_undone_items(viewer.id, run_ids)
        outcome_counts = self.c.outcomes.counts_by_run(run_ids)
        reasons = self.c.outcomes.reasons_by_run(run_ids)
        current_names = {r.id: r.name for r in self.c.rules.list_for_user(viewer.id)}
        actors = self.c.rules.usernames([a for r in runs for a in (r.started_by, r.undone_by) if a])
        scopes: Dict[str, Set[str]] = {}
        group_ids: Set[str] = set()
        for run in runs:
            for row in summary.get(run.id, []):
                if row["target_type"] in ("collection", "model_collection"):
                    scopes.setdefault(COLLECTION_SCOPES[run.subject], set()).add(row["target_id"])
                elif row["target_type"] == "user_group":
                    group_ids.add(row["target_id"])
        names = {scope: self.c.writer.collection_names(scope, ids, viewer.id) for scope, ids in scopes.items()}
        group_names = self._group_names(group_ids)
        entries = []
        for run in runs:
            collections, tags, groups, other = [], [], [], []
            pending = 0
            for row in summary.get(run.id, []):
                live = row["total"] - row["undone"]
                pending += live
                if row["target_type"] in ("collection", "model_collection"):
                    current = names.get(COLLECTION_SCOPES[run.subject], {}).get(row["target_id"])
                    collections.append({"id": row["target_id"], "name": current or row["target_name"],
                                        "count": row["total"], "live": live, "created": bool(row["created"])})
                elif row["target_type"] == "tag":
                    tags.append({"id": row["target_id"], "name": row["target_name"], "count": row["total"], "live": live})
                elif row["target_type"] == "user_group":
                    groups.append({"id": row["target_id"], "name": group_names.get(row["target_id"]) or row["target_name"],
                                   "count": row["total"], "live": live})
                else:
                    definition = self.c.registry.action(row["action_kind"])
                    other.append({"action": row["action_kind"],
                                  "label": definition.label if definition else row["action_kind"],
                                  "done_label": definition.done_label if definition else "",
                                  "count": row["total"], "live": live})
            counts = outcome_counts.get(run.id, {})
            entry = run.base_dict()
            entry["rule_name"] = current_names.get(run.rule_id, run.rule_name)
            entry["undone"] = undone.get(run.id, 0)
            entry["can_undo"] = pending > 0 and not (run.status == "running" and run.kind == "backfill")
            entry["changes"] = {"collections": collections, "tags": tags, "groups": groups, "other": other}
            entry["outcomes"] = {
                "updated": run.applied, "skipped": counts.get("skipped", 0), "waiting": counts.get("waiting", 0),
            }
            entry["reasons"] = reasons.get(run.id, [])
            entry["started_by_name"] = actors.get(run.started_by)
            entry["undone_by_name"] = actors.get(run.undone_by)
            entries.append(entry)
        return {"runs": entries, "next_before": runs[-1].id if len(runs) == limit else None}

    def undo_run(self, user: Any, run_id: str) -> Dict[str, Any]:
        viewer = viewer_of(user)
        run = self.c.runs.get_run(viewer.id, run_id)
        if run is None:
            raise errors.OrganizeError("run_not_found", "Nothing to undo", 404)
        if self.jobs.active_for_run(viewer.id, run_id) is not None:
            raise errors.OrganizeError("job_running", "Wait for the rule to finish before undoing", 409)
        undone, skipped, done_ids, undone_items = 0, 0, [], set()
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
                undone_items.add((application.item_type, application.item_id))
            except Exception:
                logger.warning("Auto-organize undo of %s failed", application.id, exc_info=True)
                skipped += 1
                continue
            done_ids.append(application.id)
        self.c.runs.mark_undone(viewer.id, done_ids)
        if not self.c.runs.pending_applications(viewer.id, run_id):
            self.c.runs.mark_run_undone(viewer.id, run_id, viewer.actor)
        entries = self.activity(user, None, run.rule_id, None, 200)["runs"]
        current = next((e for e in entries if e["id"] == run_id), None)
        return {"undone": undone, "items": len(undone_items), "skipped": skipped, "run": current}

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
