import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional

from src.features.elixirs.assistant_edits import EditPlan, EditRefused, Planner, prompt_of, usable_modes
from src.features.elixirs.coordinator import BrewRefused
from src.features.elixirs.errors import AssistantRefused, SessionNotFoundError
from src.features.elixirs.follow import FOLLOW, followed_issues, read_followed, session_mode_of
from src.features.elixirs.graph import _Graph, validate_graph, writer_target
from src.features.elixirs.identity import asset_key, media_ref
from src.features.elixirs.media import unavailable_assets
from src.features.elixirs.records import ACTIVE_RUN_STATUSES, RevisionConflict
from src.features.elixirs.sessions import LAYOUT_KEYS, SessionModeMissing, apply_overrides, mode_payload
from src.features.forms.binding import MEDIA_FIELD_TYPES

if TYPE_CHECKING:
    from src.features.elixirs.components import ElixirsComponents

logger = logging.getLogger(__name__)

RUN_MODE_LABELS = {"review": "Choose outputs between presets", "automatic": "Run all branches"}
MAX_PROBLEMS = 20
MAX_TEXT = 600
MAX_VALUE = 300
MAX_FIELDS = 60
ASSISTANT_ORIGIN_KIND = "assistant"
ASSISTANT_ORIGIN_LABEL = "PotionAI"


def conflict_message(current: int) -> str:
    return (
        f"The elixir changed since you read it (it is now at revision {current}). "
        "Call get_elixir again, then redo the edit with the new revision."
    )


def _short(text: Any, limit: int = MAX_TEXT) -> Any:
    if isinstance(text, str) and len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _bounded(value: Any) -> Any:
    if isinstance(value, str):
        return _short(value, MAX_VALUE)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return f"list of {len(value)}"
    if isinstance(value, dict):
        return f"object with {len(value)} keys"
    return _short(repr(value), MAX_VALUE)


class SessionWriter:
    def __init__(self, session_repository: Any, plugin_registry: Any, version_repository: Any, file_preset_repository: Any, notifier: Any = None) -> None:
        self.session_repository = session_repository
        self.plugin_registry = plugin_registry
        self.version_repository = version_repository
        self.file_preset_repository = file_preset_repository
        self.notifier = notifier

    def find(self, user_id: str, preset_id: str, name: str) -> Optional[Dict[str, Any]]:
        session = self.session_repository.get_by_user_preset_and_name(user_id, preset_id, name)
        if session is None:
            return None
        return {
            "session_id": session.id,
            "preset_id": session.preset_id,
            "name": session.name,
            "version": session.version,
            "data": session.data or {},
        }

    def write(self, user_id: str, session_id: str, name: str, mode: str, payload: Dict[str, Any], base_version: Optional[int]) -> Dict[str, Any]:
        from src.features.sessions import operations
        from src.features.sessions.dto import UpdateSessionRequest
        from src.features.sessions.live import WriteOrigin, announce

        origin = WriteOrigin(ASSISTANT_ORIGIN_KIND, ASSISTANT_ORIGIN_LABEL)
        saved = operations.update_session(
            self.session_repository,
            self.plugin_registry,
            self.version_repository,
            self.file_preset_repository,
            user_id,
            session_id,
            UpdateSessionRequest(name=name, data=payload, mode=mode, base_version=base_version),
            origin,
        )
        announce(self.notifier, user_id, saved, origin)
        return saved


@dataclass
class Prepared:
    record: Any
    plan: EditPlan
    problems: List[Dict[str, Any]]


class ElixirAssistant:
    def __init__(self, components: "ElixirsComponents", session_writer: Optional[SessionWriter] = None, notifier: Any = None, storage_root: Optional[Callable[[], str]] = None) -> None:
        self.components = components
        self.session_writer = session_writer
        self.notifier = notifier
        self.storage_root = storage_root

    def user(self, user_id: str):
        user = self.components.coordinator.port.user(user_id)
        if user is None:
            raise AssistantRefused("Your account could not be found.")
        return user

    def record(self, user_id: str, elixir_id: Any):
        if not isinstance(elixir_id, str) or not elixir_id:
            raise AssistantRefused("No elixir is open. Ask the user to open the elixir they mean in an Elixir tab.")
        record = self.components.repository.get(elixir_id, user_id)
        if record is None:
            raise AssistantRefused("That elixir does not exist any more. Ask the user to open another one.")
        return record

    def read_session(self, user, session_id: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(session_id, str) or not session_id:
            return None
        try:
            return self.components.sessions.get_session(user, session_id)
        except SessionNotFoundError:
            return None

    def notify(self, user_id: str, elixir_id: str, revision: Optional[int], run_id: Optional[str] = None) -> None:
        if self.notifier is None:
            return
        message: Dict[str, Any] = {"type": "elixir_changed", "elixir_id": elixir_id, "revision": revision}
        if run_id:
            message["run_id"] = run_id
        try:
            self.notifier.schedule_send(user_id, message)
        except Exception:
            logger.warning("Could not push elixir %s change to its user", elixir_id, exc_info=True)

    def _catalog(self, nodes) -> Dict[str, Any]:
        return self.components.catalog(node.get("preset_id") for node in nodes if node.get("kind") == "preset")

    def _problems(self, user, record_definition: Mapping[str, Any], run_mode: str) -> List[Dict[str, Any]]:
        nodes = record_definition.get("nodes") or []
        report = validate_graph(
            record_definition,
            run_mode=run_mode,
            catalog=self._catalog(nodes),
            contracts=self.components.contracts(),
            limits=self.components.limits(),
            unavailable=unavailable_assets(user, nodes, self.components.resolver),
        )
        issues = report["issues"] + followed_issues(nodes, read_followed(user, nodes, self.components.sessions.get_session))
        return [
            {key: issue[key] for key in ("severity", "message", "node_id") if issue.get(key)}
            for issue in issues
        ]

    def summary(self, user_id: str, elixir_id: Any, include_presets: bool = False) -> Dict[str, Any]:
        user = self.user(user_id)
        record = self.record(user_id, elixir_id)
        definition = record.definition
        nodes = definition.get("nodes") or []
        edges = definition.get("edges") or []
        catalog = self._catalog(nodes)
        contracts = self.components.contracts()
        graph = _Graph(definition, catalog, contracts)
        planner = Planner(definition, catalog_for=lambda ids: catalog, contracts=contracts, read_session=lambda _: None, find_session=lambda *_: None)
        depth: Dict[str, int] = {}

        def column(node_id: str, seen=()) -> int:
            if node_id in depth:
                return depth[node_id]
            parent = planner.parent(node_id)
            depth[node_id] = 0 if parent is None or parent in seen else column(parent, (*seen, node_id)) + 1
            return depth[node_id]

        steps = []
        for node in nodes:
            steps.append(self._step(node, graph, planner, nodes, edges, column(node["id"])))
        problems = self._problems(user, definition, record.run_mode)
        summary: Dict[str, Any] = {
            "elixir_id": record.id,
            "name": record.name,
            "revision": record.revision,
            "brew_mode": record.run_mode,
            "brew_mode_label": RUN_MODE_LABELS.get(record.run_mode, record.run_mode),
            "steps": steps,
            "connections": [
                {"id": edge["id"], "from": edge["source"], "from_port": edge["source_handle"], "to": edge["target"], "to_port": edge["target_handle"]}
                for edge in edges
            ],
            "problems": problems[:MAX_PROBLEMS],
            "problem_count": len(problems),
        }
        summary.update(self._brew_state(user_id, record))
        if include_presets:
            ids = sorted({preset_id for preset_id, _ in contracts})
            summary["usable_presets"] = usable_modes(self.components.catalog(ids), contracts)
        return summary

    def _step(self, node, graph: _Graph, planner: Planner, nodes, edges, column: int) -> Dict[str, Any]:
        kind = node.get("kind")
        step: Dict[str, Any] = {"id": node["id"], "kind": {"llm": "writer", "media": "files"}.get(kind, "preset"), "label": graph.label(node), "column": column}
        incoming = [edge for edge in edges if edge["target"] == node["id"]]
        if incoming:
            step["inputs"] = {edge["target_handle"]: edge["source"] for edge in incoming}
        if kind == "media":
            step["media_type"] = node.get("media_type")
            step["file_count"] = len(node.get("assets") or [])
            return step
        if kind == "llm":
            step["instruction"] = _short(node.get("instruction") or "")
            if node.get("instruction_chips"):
                step["instruction_chip_count"] = len(node["instruction_chips"])
            step["writes_for"] = node.get("target_node_id")
            step["prompt_goes_to"] = [edge["target"] for edge in edges if edge["source"] == node["id"]]
            step["styled_for"] = writer_target(nodes, edges, node)
            return step
        contract = graph.contract(node)
        step.update(preset_id=node.get("preset_id"), mode=node.get("mode"))
        if contract is not None:
            step["preset"] = graph.catalog[node["preset_id"]]["name"]
            step["mode_label"] = contract.label
        session_mode = session_mode_of(node)
        session: Dict[str, Any] = {"mode": session_mode}
        source = node.get("session_ref") if session_mode == FOLLOW else node.get("session_source")
        if source and source.get("name"):
            session["name"] = source.get("name")
        if session_mode != FOLLOW and node.get("overrides"):
            session["edited"] = sorted(node["overrides"])
        step["session"] = session
        return step

    def _brew_state(self, user_id: str, record) -> Dict[str, Any]:
        runs = self.components.coordinator.store.list_runs(record.id, user_id)
        if not runs:
            return {"last_brew": None, "waiting_for_choice": []}
        latest = runs[0]
        run = self.components.coordinator.store.get_run(latest["id"], user_id) or {}
        state = {
            "last_brew": {
                "id": latest["id"],
                "status": latest["status"],
                "created_at": latest.get("created_at"),
                "error": _short(latest.get("error")),
                "steps": {node_id: entry.get("status") for node_id, entry in (run.get("node_states") or {}).items()},
            },
            "waiting_for_choice": [
                {"step": lane["step"], "to": lane["to"], "outputs": lane["output_count"], "picked": len(lane["picked"])}
                for lane in self.lanes(run)
            ],
        }
        return state

    def lanes(self, run: Mapping[str, Any]) -> List[Dict[str, Any]]:
        if not run or run.get("status") not in ACTIVE_RUN_STATUSES or run.get("cancel_requested_at"):
            return []
        graph = run.get("graph") or {}
        kinds = {node["id"]: node.get("kind") for node in graph.get("nodes") or []}
        checkpoints = run.get("checkpoints") or {}
        lanes = []
        hidden = set()
        for edge in graph.get("edges") or []:
            checkpoint = checkpoints.get(edge["id"])
            if edge["id"] in hidden or not checkpoint or checkpoint.get("confirmed") is not None or not checkpoint.get("eligible"):
                continue
            partners = [edge_id for edge_id in checkpoint.get("linked") or [] if edge_id in checkpoints]
            hidden.update(partners)
            targets = [edge["target"]] + [other["target"] for other in graph.get("edges") or [] if other["id"] in partners]
            lanes.append({
                "edge_id": edge["id"],
                "step": edge["source"],
                "to": sorted(targets, key=lambda target: kinds.get(target) == "llm"),
                "revision": checkpoint["revision"],
                "eligible": checkpoint["eligible"],
                "output_count": len(checkpoint["eligible"]),
                "picked": list(checkpoint.get("draft") or []),
                "error": checkpoint.get("error"),
            })
        return lanes

    def step_settings(self, user_id: str, elixir_id: Any, step_id: Any, names: Optional[List[str]] = None) -> Dict[str, Any]:
        user = self.user(user_id)
        record = self.record(user_id, elixir_id)
        nodes = record.definition.get("nodes") or []
        node = next((item for item in nodes if item["id"] == step_id), None)
        if node is None:
            known = ", ".join(item["id"] for item in nodes) or "none"
            raise AssistantRefused(f"There is no step '{step_id}'. Steps: {known}. Use the ids from get_elixir.")
        catalog = self._catalog(nodes)
        graph = _Graph(record.definition, catalog, self.components.contracts())
        if node.get("kind") == "llm":
            return {
                "step": node["id"], "kind": "writer", "label": graph.label(node),
                "instruction": node.get("instruction") or "",
                "instruction_chips": node.get("instruction_chips") or {},
                "writes_for": node.get("target_node_id"),
            }
        if node.get("kind") != "preset":
            return {"step": node["id"], "kind": "files", "label": graph.label(node), "media_type": node.get("media_type"), "file_count": len(node.get("assets") or [])}
        session_mode = session_mode_of(node)
        if session_mode == FOLLOW:
            current = self.read_session(user, (node.get("session_ref") or {}).get("id"))
            try:
                settings = mode_payload((current or {}).get("data") or {}, node.get("mode")) if current else dict(node.get("session_snapshot") or {})
            except SessionModeMissing:
                settings = dict(node.get("session_snapshot") or {})
        else:
            settings = apply_overrides(node.get("session_snapshot"), node.get("overrides"))
        settings = {key: value for key, value in settings.items() if key not in LAYOUT_KEYS}
        planner = Planner(record.definition, catalog_for=lambda ids: catalog, contracts=self.components.contracts(), read_session=lambda _: None, find_session=lambda *_: None)
        fields = planner.form_fields(node, settings)
        form = settings.get("formData") or {}
        wanted = [name for name in (names or []) if isinstance(name, str)]
        listed = wanted or sorted(fields)
        unknown = [name for name in wanted if name not in fields]
        rows = []
        for name in listed[:MAX_FIELDS]:
            spec = fields.get(name)
            if spec is None:
                continue
            row = {"name": name, "type": spec.get("type"), "value": _bounded(form.get(name))}
            if spec.get("type") in MEDIA_FIELD_TYPES:
                row["filled_by"] = "a connection"
            rows.append(row)
        result: Dict[str, Any] = {
            "step": node["id"],
            "kind": "preset",
            "label": graph.label(node),
            "preset_id": node.get("preset_id"),
            "mode": node.get("mode"),
            "session_mode": session_mode,
            "edits_go_to": "the followed session (every step and tab using it sees the change)" if session_mode == FOLLOW else "this step only",
            "prompt": _short(prompt_of(settings, "prompt")),
            "negative_prompt": _short(prompt_of(settings, "negativePrompt")),
            "fields": rows,
            "field_count": len(fields),
            "has_more": len(listed) > MAX_FIELDS,
        }
        if any(edge["target"] == node["id"] and edge["target_handle"] == "prompt" for edge in record.definition.get("edges") or []):
            result["prompt_note"] = "A prompt writer replaces this prompt when the elixir brews."
        if unknown:
            result["not_found"] = unknown
        return result

    def prepare(self, user_id: str, elixir_id: Any, revision: Any, operations: List[Any]) -> Prepared:
        user = self.user(user_id)
        record = self.record(user_id, elixir_id)
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise AssistantRefused("Pass 'revision', the number get_elixir returned, so the edit applies to the elixir you read.")
        if revision != record.revision:
            raise AssistantRefused(conflict_message(record.revision))
        planner = Planner(
            record.definition,
            catalog_for=lambda ids: self.components.catalog(ids),
            contracts=self.components.contracts(),
            read_session=lambda session_id: self.read_session(user, session_id),
            find_session=lambda preset_id, name: self.session_writer.find(user_id, preset_id, name) if self.session_writer else None,
        )
        try:
            plan = planner.apply(operations)
        except EditRefused as refused:
            raise AssistantRefused(str(refused)) from None
        if plan.session_writes and self.session_writer is None:
            raise AssistantRefused("Followed sessions cannot be changed here. Switch the step to copy first.")
        problems = self._problems(user, plan.definition, record.run_mode) if plan.changed else []
        return Prepared(record=record, plan=plan, problems=problems)

    def apply(self, user_id: str, elixir_id: Any, revision: Any, operations: List[Any]) -> Dict[str, Any]:
        from src.features.sessions.operations.save import SessionVersionConflict

        prepared = self.prepare(user_id, elixir_id, revision, operations)
        record, plan = prepared.record, prepared.plan
        written = []
        for write in plan.session_writes.values():
            try:
                saved = self.session_writer.write(user_id, write.session_id, write.session_name, write.mode, write.payload, write.base_version)
            except SessionVersionConflict:
                raise AssistantRefused(
                    f"The session '{write.session_name}' changed since the elixir last saw it. "
                    "Call get_step_settings again and redo the change."
                ) from None
            written.append({"session": write.session_name, "version": saved.get("version")})
        current_revision = record.revision
        if plan.changed:
            try:
                updated = self.components.repository.update(record.id, user_id, record.revision, definition=plan.definition)
            except RevisionConflict as conflict:
                raise AssistantRefused(conflict_message(conflict.current_revision)) from None
            if updated is None:
                raise AssistantRefused("That elixir does not exist any more.")
            current_revision = updated.revision
        self.notify(user_id, record.id, current_revision)
        return {"revision": current_revision, "rows": plan.rows, "sessions_written": written, "problems": prepared.problems[:MAX_PROBLEMS]}

    def waiting(self, user_id: str, elixir_id: Any) -> Dict[str, Any]:
        record = self.record(user_id, elixir_id)
        runs = self.components.coordinator.store.list_runs(record.id, user_id)
        run = self.components.coordinator.store.get_run(runs[0]["id"], user_id) if runs else None
        return {"record": record, "run": run, "lanes": self.lanes(run) if run else []}

    def lane_for(self, user_id: str, elixir_id: Any, step_id: Any, to_step: Any = None) -> Dict[str, Any]:
        found = self.waiting(user_id, elixir_id)
        lanes = found["lanes"]
        if not lanes:
            raise AssistantRefused(
                "No step is waiting for a choice. Outputs are picked while a brew in 'Choose outputs between presets' mode waits; "
                "the user starts brews with Brew and continues them with Continue."
            )
        waiting_steps = ", ".join(sorted({lane["step"] for lane in lanes}))
        if step_id is None and len({lane["step"] for lane in lanes}) == 1:
            step_id = lanes[0]["step"]
        mine = [lane for lane in lanes if lane["step"] == step_id]
        if not mine:
            raise AssistantRefused(f"Step '{step_id}' is not waiting for a choice. Steps waiting: {waiting_steps}.")
        if to_step is not None:
            mine = [lane for lane in mine if to_step in lane["to"]]
            if not mine:
                raise AssistantRefused(f"Step '{step_id}' has no open choice for '{to_step}'. Pass one of: {', '.join(target for lane in self.lanes(found['run']) if lane['step'] == step_id for target in lane['to'])}.")
        if len(mine) > 1:
            raise AssistantRefused(f"Step '{step_id}' sends outputs to several steps. Pass to_step, one of: {', '.join(lane['to'][0] for lane in mine)}.")
        return {**mine[0], "run_id": found["run"]["id"], "record": found["record"], "run": found["run"]}

    def resolve_picks(self, lane: Mapping[str, Any], outputs: Any) -> List[str]:
        if not isinstance(outputs, list) or not outputs:
            raise AssistantRefused("Pass 'outputs': a list of output ids (or their numbers) from get_choice_outputs.")
        keys = [asset_key(entry) for entry in lane["eligible"]]
        picked = []
        for value in outputs:
            if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= len(keys):
                picked.append(keys[value - 1])
            elif isinstance(value, str) and value in keys:
                picked.append(value)
            elif isinstance(value, str) and value.isdigit() and 1 <= int(value) <= len(keys):
                picked.append(keys[int(value) - 1])
            else:
                raise AssistantRefused(f"{value!r} is not one of this step's outputs. Use the ids or numbers 1-{len(keys)} from get_choice_outputs.")
        return [key for key in keys if key in set(picked)]

    def choose(self, user_id: str, elixir_id: Any, step_id: Any, outputs: Any, to_step: Any = None) -> Dict[str, Any]:
        lane = self.lane_for(user_id, elixir_id, step_id, to_step)
        picked = self.resolve_picks(lane, outputs)
        try:
            self.components.coordinator.save_draft(self.user(user_id), lane["run_id"], lane["edge_id"], picked, lane["revision"])
        except BrewRefused as refused:
            raise AssistantRefused(f"{refused.message} Call get_choice_outputs again.") from None
        self.notify(user_id, lane["record"].id, lane["record"].revision, lane["run_id"])
        return {"step": lane["step"], "to": lane["to"], "picked": picked, "count": len(picked), "of": lane["output_count"]}

    def resolve_media(self, user_id: str, entry: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            return self.components.resolver(self.user(user_id), media_ref(entry))
        except Exception:
            return None
