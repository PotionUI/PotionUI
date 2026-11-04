import asyncio
import copy
import logging
import secrets
import uuid
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from src.features.elixirs.brew import (
    SEED_LIMIT,
    TEXT_BINDINGS,
    BindingFailed,
    batch_size,
    bind_input,
    bind_text,
    bind_texts,
    director_problem,
    latest_attempts,
    manifest_for,
    media_manifest,
    media_rows,
    node_status,
    outputs_from_files,
    pick_text,
    prepared_template,
    prompts_per_input,
    reach,
    resolve_seed,
    rollup_status,
    slot_parts,
    text_outputs,
    text_rows,
    texts_for_input,
    writer_rows,
)
from src.features.elixirs.catalog import Catalog
from src.features.elixirs.contracts import Contracts
from src.features.elixirs.dto import MAX_REASONING_CHARS, MAX_WRITTEN_CHARS
from src.features.elixirs.errors import MediaRefError, WriteRefused
from src.features.elixirs.graph import (
    MEDIA_SOURCE_PORTS,
    driving_edges,
    field_is_multi,
    is_text_edge,
    linked_edge_ids,
    prompt_inputs,
    writer_target,
)
from src.features.elixirs.identity import (
    ROOT_INPUT,
    asset_key,
    invocation_key,
    media_ref,
)
from src.features.elixirs.instruction import roll_instruction
from src.features.elixirs.limits import Limits
from src.features.elixirs.records import (
    ACTIVE_INVOCATION_STATUSES,
    ACTIVE_RUN_STATUSES,
    utc_now,
)
from src.features.elixirs.run_repository import RunStore
from src.features.elixirs.vocabulary import (
    TEXT,
    WRITER_INPUTS,
    WRITER_OUTPUT,
    node_label,
)
from src.features.generation.exceptions import GenerationNotFoundException
from src.features.generation.prompt_batch import per_prompt_quantity

logger = logging.getLogger(__name__)

TERMINAL_RUN_STATUSES = ("completed", "partial", "cancelled", "failed", "interrupted")
MAX_WRITER_ERROR = 300
MAX_FINISHED_REMEMBERED = 1024


def _reasoning(result: Optional[Mapping[str, Any]]) -> Optional[str]:
    text = str((result or {}).get("reasoning") or "").strip()
    return text[:MAX_REASONING_CHARS] or None


def _short(message: str) -> str:
    message = " ".join(str(message).split())
    return message if len(message) <= MAX_WRITER_ERROR else message[: MAX_WRITER_ERROR - 1].rstrip() + "…"


class BrewRefused(Exception):
    def __init__(self, code: str, message: str, status: int = 422, node_id: Optional[str] = None, **extra: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.node_id = node_id
        self.extra = extra

    def to_detail(self) -> Dict[str, Any]:
        detail = {"code": self.code, "message": self.message, **self.extra}
        if self.node_id:
            detail["node_id"] = self.node_id
        return detail


def _ports_for(node: Mapping[str, Any], contracts: Contracts) -> Dict[str, Dict[str, Any]]:
    if node.get("kind") == "media":
        media_type = node.get("media_type")
        return {"outputs": {MEDIA_SOURCE_PORTS[media_type]: media_type} if media_type else {}, "inputs": {}}
    if node.get("kind") == "llm":
        return {
            "outputs": {WRITER_OUTPUT: TEXT},
            "inputs": {
                name: {"media_type": media_type, "binding": {"kind": "writer_media"}, "label": label}
                for name, (label, media_type) in WRITER_INPUTS.items()
            },
        }
    contract = contracts.get((node.get("preset_id"), node.get("mode")))
    if contract is None:
        return {"outputs": {}, "inputs": {}}
    inputs = {
        name: {"media_type": port.media_type, "binding": port.target.model_dump(exclude_none=True), "label": port.label}
        for name, port in contract.inputs.items()
    }
    inputs.update({name: {"media_type": TEXT, "binding": {"kind": name}, "label": label} for name, label in prompt_inputs(contract).items()})
    return {"outputs": {name: port.media_type for name, port in contract.outputs.items()}, "inputs": inputs}


def _with_roles(by_id: Mapping[str, Mapping[str, Any]], frozen: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    incoming: Dict[str, List[Dict[str, Any]]] = {}
    for edge in frozen:
        incoming.setdefault(edge["target"], []).append(edge)
    driving = {edge["id"] for edges in incoming.values() for edge in driving_edges(by_id, edges)}
    for edge in frozen:
        edge["role"] = "drive" if edge["id"] in driving else "side"
    return frozen


def _target_of(nodes: Iterable[Mapping[str, Any]], edges: Iterable[Mapping[str, Any]], node: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    nodes = list(nodes)
    target_id = writer_target(nodes, list(edges), node)
    target = next((item for item in nodes if item["id"] == target_id), None)
    if target is None:
        return None
    return {"node_id": target_id, "preset_id": target.get("preset_id"), "mode": target.get("mode")}


def _batchable(node: Mapping[str, Any], template: Mapping[str, Any], catalog: Optional[Catalog]) -> bool:
    form_data = template.get("form_data") or {}
    if isinstance(form_data.get("video_director"), Mapping) or isinstance(form_data.get("music_director"), Mapping):
        return False
    return bool(((catalog or {}).get(node.get("preset_id")) or {}).get("prompt_batch"))


def _frozen_binding(node: Mapping[str, Any], target_input: Mapping[str, Any], catalog: Optional[Catalog]) -> Dict[str, Any]:
    binding = dict(target_input["binding"])
    if binding.get("kind") == "form_field":
        binding["multi"] = field_is_multi(catalog, node, binding["field"])
    return binding


class Coordinator:
    def __init__(
        self,
        store: RunStore,
        port: Any,
        limits_provider: Optional[Callable[[], Limits]] = None,
        boot_id: Optional[str] = None,
        random_below: Callable[[int], int] = secrets.randbelow,
    ) -> None:
        self.store = store
        self.port = port
        self.limits_provider = limits_provider or Limits
        self.boot_id = boot_id or uuid.uuid4().hex
        self.random_below = random_below
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue] = None
        self._cancel_sent: set = set()
        self._writers: Dict[str, asyncio.Task] = {}
        self._stopping: set = set()
        self._dirty: set = set()
        self._sweep = False
        self._finished: Dict[str, None] = {}

    def start_graph_run(
        self,
        user,
        elixir: Mapping[str, Any],
        contracts: Contracts,
        templates: Mapping[str, Mapping[str, Any]],
        run_mode: str,
        idempotency_key: str,
        catalog: Optional[Catalog] = None,
        sessions: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> Dict[str, Any]:
        definition = elixir["definition"]
        nodes = list(definition.get("nodes") or [])
        by_id = {node["id"]: node for node in nodes}
        edges = list(definition.get("edges") or [])
        with self.store.transaction() as cursor:
            existing = self.store.find_run_by_key(cursor, user.id, idempotency_key)
        if existing is not None:
            if existing.get("elixir_id") != elixir["id"]:
                raise BrewRefused("idempotency_conflict", "That brew request was already used for another elixir.", status=409)
            return existing

        ports = {node["id"]: _ports_for(node, contracts) for node in nodes}
        incoming: Dict[str, List[Mapping[str, Any]]] = {}
        frozen_edges = []
        for edge in edges:
            target_input = ports.get(edge["target"], {}).get("inputs", {}).get(edge["target_handle"])
            if target_input is None:
                raise BrewRefused("unknown_input", f"A connection points at an input that no longer exists ({edge['target_handle']}).")
            binding = _frozen_binding(by_id.get(edge["target"], {}), target_input, catalog)
            frozen = {**edge, "binding": binding, "media_type": target_input["media_type"]}
            frozen_edges.append(frozen)
            incoming.setdefault(edge["target"], []).append(frozen)
        _with_roles(by_id, frozen_edges)

        frozen_templates: Dict[str, Dict[str, Any]] = {}
        node_states: Dict[str, Dict[str, Any]] = {}
        pinned: Dict[str, List[Dict[str, Any]]] = {}
        roots: List[str] = []
        for node in nodes:
            label = node_label(node)
            if node.get("kind") == "media":
                assets = []
                for index, asset in enumerate(node.get("assets") or []):
                    try:
                        resolved = self.port.resolve(user, media_ref(asset))
                    except MediaRefError:
                        raise BrewRefused(
                            "unavailable_media",
                            f"{label}: file {index + 1} is no longer available. Choose it again or remove it.",
                            node_id=node["id"],
                        ) from None
                    assets.append({
                        **media_ref(asset),
                        "media_type": str(resolved.get("media_type") or asset.get("media_type") or "").lower() or None,
                        "original_filename": resolved.get("original_filename") or asset.get("original_filename"),
                        "preview_url": asset.get("preview_url"),
                    })
                if not assets:
                    raise BrewRefused("no_media", f"{label} has no files yet.", node_id=node["id"])
                pinned[node["id"]] = assets
                port_name = MEDIA_SOURCE_PORTS[node["media_type"]]
                node_states[node["id"]] = {"status": "done", "manifest": media_manifest(assets, port_name)}
                continue
            if node.get("kind") == "llm":
                node_states[node["id"]] = {"status": "waiting"}
                if not incoming.get(node["id"]):
                    roots.append(node["id"])
                continue
            template = templates.get(node["id"])
            if not isinstance(template, Mapping):
                raise BrewRefused("missing_template", f"{label} has no prepared request. Reload the elixir and brew again.", node_id=node["id"])
            if template.get("preset_id") != node.get("preset_id") or template.get("mode") != node.get("mode"):
                raise BrewRefused("template_mismatch", f"{label}'s prepared request is for another preset or mode.", node_id=node["id"])
            unbound = [edge["binding"]["field"] for edge in incoming.get(node["id"], []) if edge["binding"].get("kind") == "form_field"]
            frozen_templates[node["id"]] = prepared_template(template, unbound)
            self._check_director(label, node["id"], frozen_templates[node["id"]], incoming.get(node["id"], []))
            node_states[node["id"]] = {"status": "waiting"}
            if not incoming.get(node["id"]):
                roots.append(node["id"])

        limits = self.limits_provider()
        root_rows = {node_id: writer_rows(by_id[node_id], [(None, ROOT_INPUT, None, 0)]) for node_id in roots}
        if sum(len(rows) for rows in root_rows.values()) > limits.max_invocations:
            raise BrewRefused("too_many_generations", f"One brew can start at most {limits.max_invocations} generations.")
        with self.store.transaction() as cursor:
            existing = self.store.find_run_by_key(cursor, user.id, idempotency_key)
            if existing is not None:
                return existing
            run_id = self.store.insert_run(cursor, {
                "elixir_id": elixir["id"],
                "user_id": user.id,
                "elixir_revision": elixir["revision"],
                "scope": "graph",
                "run_mode": run_mode,
                "status": "pending",
                "graph": {
                    "nodes": nodes,
                    "edges": frozen_edges,
                    "ports": ports,
                    "descendants": reach(edges),
                    "targets": {node["id"]: _target_of(nodes, edges, node) for node in nodes if node.get("kind") == "llm"},
                    "sessions": dict(sessions or {}),
                    "prompt_batch": {
                        node_id: _batchable(by_id[node_id], template, catalog) for node_id, template in frozen_templates.items()
                    },
                },
                "templates": frozen_templates,
                "pinned_inputs": pinned,
                "node_states": node_states,
                "checkpoints": {},
                "idempotency_key": idempotency_key,
            })
            for node_id, rows in root_rows.items():
                for _, input_key, _, ordinal in rows:
                    self.store.insert_invocation(cursor, {
                        "run_id": run_id,
                        "user_id": user.id,
                        "node_id": node_id,
                        "edge_id": None,
                        "ordinal": ordinal,
                        "input_key": input_key,
                        "attempt": 1,
                        "idempotency_key": invocation_key(run_id, node_id, None, input_key, 1),
                        "status": "pending",
                    })
        self._settle(run_id)
        self.wake()
        return self.store.get_run(run_id)

    def start_node_run(
        self,
        user,
        elixir: Mapping[str, Any],
        contracts: Contracts,
        node_id: str,
        template: Mapping[str, Any],
        idempotency_key: str,
        catalog: Optional[Catalog] = None,
        sessions: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> Dict[str, Any]:
        definition = elixir["definition"]
        nodes = {node["id"]: node for node in definition.get("nodes") or []}
        edges = list(definition.get("edges") or [])
        node = nodes.get(node_id)
        if node is None:
            raise BrewRefused("node_not_found", "That node is not in this elixir.", status=404)
        label = node_label(node)
        if node.get("kind") == "media":
            raise BrewRefused("not_a_preset_node", f"{label} is a media source; there is nothing to run.", node_id=node_id)
        writer = node.get("kind") == "llm"
        target = _target_of(nodes.values(), edges, node) if writer else None
        if writer:
            usable = isinstance(template, Mapping) and target is not None and (template.get("preset_id"), template.get("mode")) == (target["preset_id"], target["mode"])
            templates = {target["node_id"]: prepared_template(template, [])} if usable else {}
        elif not isinstance(template, Mapping) or template.get("preset_id") != node.get("preset_id") or template.get("mode") != node.get("mode"):
            raise BrewRefused("template_mismatch", f"{label}'s prepared request is for another preset or mode.", node_id=node_id)
        incoming = [edge for edge in edges if edge["target"] == node_id]
        if len([edge for edge in incoming if not is_text_edge(nodes, edge)]) > 1:
            raise BrewRefused("too_many_inputs", f"{label} can take only one connected input.", node_id=node_id)
        ports = {node_id: _ports_for(node, contracts)}
        ancestors = reach(edges, forward=False).get(node_id, [])
        descendants = reach(edges)
        related = {node_id, *ancestors, *descendants.get(node_id, [])}
        frozen_edges = []
        for edge in incoming:
            target_input = ports[node_id]["inputs"].get(edge["target_handle"])
            if target_input is None:
                raise BrewRefused("unknown_input", f"{label} has no {edge['target_handle']} input any more.", node_id=node_id)
            frozen_edges.append({**edge, "binding": _frozen_binding(node, target_input, catalog), "media_type": target_input["media_type"]})
        _with_roles(nodes, frozen_edges)
        onward, peers, peer_edges = ([], [], [])
        if not writer and (elixir.get("run_mode") or "review") != "automatic":
            onward, peers, peer_edges = self._onward(nodes, edges, node, contracts, catalog)
        drive = next((edge for edge in frozen_edges if edge["role"] == "drive"), None)
        sides = [edge for edge in frozen_edges if edge["role"] == "side"]
        unbound = [edge["binding"]["field"] for edge in frozen_edges if edge["binding"].get("kind") == "form_field"]
        if not writer:
            templates = {node_id: prepared_template(template, unbound)}
            self._check_director(label, node_id, templates[node_id], frozen_edges)

        with self.store.transaction() as cursor:
            existing = self.store.find_run_by_key(cursor, user.id, idempotency_key)
            if existing is not None:
                if existing.get("elixir_id") != elixir["id"] or existing.get("scope_node_id") != node_id:
                    raise BrewRefused("idempotency_conflict", "That run request was already used for something else.", status=409)
                return existing
            history = self.store.runs_for_elixir(cursor, elixir["id"])
            for past in history:
                if past["status"] not in ACTIVE_RUN_STATUSES:
                    continue
                busy = {
                    item["node_id"]
                    for item in self.store.run_invocations(cursor, past["id"])
                    if item["status"] in ACTIVE_INVOCATION_STATUSES
                }
                if busy & related:
                    raise BrewRefused(
                        "related_work_running",
                        f"{label} or a node connected to it is still generating. Wait for it to finish or stop that brew.",
                        status=409,
                        node_id=node_id,
                    )
            results = self.store.results_for(cursor, elixir["id"])
            selection: List[Dict[str, Any]] = []
            source_run_id = None
            base_run_id = None
            if drive is not None:
                for past in history:
                    checkpoint = past["checkpoints"].get(drive["id"])
                    if checkpoint and checkpoint.get("confirmed"):
                        by_key = {asset_key(entry): entry for entry in checkpoint["eligible"]}
                        selection = [by_key[key] for key in checkpoint["confirmed"]]
                        source_run_id = base_run_id = past["id"]
                        break
                    if past["scope"] == "node" and past.get("scope_edge_id") == drive["id"] and past["pinned_inputs"].get("selection"):
                        selection = past["pinned_inputs"]["selection"]
                        source_run_id = past["pinned_inputs"].get("source_run_id")
                        base_run_id = past["id"]
                        break
                source_label = node_label(nodes.get(drive["source"], {"id": drive["source"]}))
                if not selection:
                    raise BrewRefused(
                        "no_inputs",
                        f"{label} has no chosen input from {source_label} yet. Brew the elixir first.",
                        status=409,
                        node_id=node_id,
                    )
                current = results.get(drive["source"])
                if current and nodes.get(drive["source"], {}).get("kind") != "media" and current.get("run_id") != source_run_id:
                    raise BrewRefused(
                        "inputs_changed",
                        f"{source_label} has newer outputs. Brew the elixir again to choose new inputs for {label}.",
                        status=409,
                        node_id=node_id,
                    )
            side_texts: Dict[str, List[Dict[str, Any]]] = {}
            for side in sides:
                texts = ((results.get(side["source"]) or {}).get("manifest") or {}).get(WRITER_OUTPUT) or []
                if not texts:
                    raise BrewRefused(
                        "no_inputs",
                        f"{label} has no written prompt from {node_label(nodes.get(side['source'], {'id': side['source']}))} yet. Brew the elixir first.",
                        status=409,
                        node_id=node_id,
                    )
                side_texts[side["id"]] = texts
            prompt_batch = {} if writer else {node_id: _batchable(node, templates[node_id], catalog)}
            planned = {
                "graph": {"nodes": [node], "edges": frozen_edges, "prompt_batch": prompt_batch},
                "templates": templates,
                "node_states": {},
                "pinned_inputs": {"sides": side_texts},
            }
            if drive is None:
                selection_rows = writer_rows(node, [(None, ROOT_INPUT, None, 0)])
            else:
                selection_rows = self._rows_for(planned, drive, selection)
            limits = self.limits_provider()
            if len(selection_rows) > limits.max_invocations:
                raise BrewRefused("too_many_generations", f"One brew can start at most {limits.max_invocations} generations.", node_id=node_id)
            run_id = self.store.insert_run(cursor, {
                "elixir_id": elixir["id"],
                "user_id": user.id,
                "elixir_revision": elixir["revision"],
                "scope": "node",
                "scope_node_id": node_id,
                "scope_edge_id": drive["id"] if drive else None,
                "base_run_id": base_run_id,
                "run_mode": elixir.get("run_mode") or "review",
                "status": "pending",
                "graph": {
                    "nodes": [node],
                    "edges": [*frozen_edges, *onward],
                    "peers": peers,
                    "peer_edges": peer_edges,
                    "ports": ports,
                    "descendants": descendants,
                    "targets": {node_id: target} if writer else {},
                    "sessions": dict(sessions or {}),
                    "prompt_batch": prompt_batch,
                },
                "templates": templates,
                "pinned_inputs": {"selection": selection, "source_run_id": source_run_id, "sides": side_texts},
                "node_states": {node_id: {"status": "waiting"}},
                "checkpoints": {},
                "idempotency_key": idempotency_key,
            })
            for edge_id, input_key, entry, ordinal in selection_rows:
                self.store.insert_invocation(cursor, {
                    "run_id": run_id,
                    "user_id": user.id,
                    "node_id": node_id,
                    "edge_id": edge_id,
                    "ordinal": ordinal,
                    "input_key": input_key,
                    "input_ref": entry,
                    "source_node_id": drive["source"] if drive else None,
                    "source_run_id": source_run_id,
                    "source_invocation_id": (entry or {}).get("source_invocation_id"),
                    "source_ordinal": (entry or {}).get("source_ordinal"),
                    "source_file_index": (entry or {}).get("file_index"),
                    "attempt": 1,
                    "idempotency_key": invocation_key(run_id, node_id, edge_id, input_key, 1),
                    "status": "pending",
                })
        self._settle(run_id)
        self.wake()
        return self.store.get_run(run_id)

    @staticmethod
    def _onward(nodes, edges, node, contracts: Contracts, catalog: Optional[Catalog]):
        outgoing = [edge for edge in edges if edge["source"] == node["id"] and edge["target"] in nodes]
        targets = {edge["target"] for edge in outgoing}
        driving = {
            edge["id"]
            for target in targets
            for edge in driving_edges(nodes, [item for item in edges if item["target"] == target])
        }
        onward = []
        for edge in outgoing:
            if edge["id"] not in driving:
                continue
            target = nodes[edge["target"]]
            target_input = _ports_for(target, contracts)["inputs"].get(edge["target_handle"])
            if target_input is None:
                continue
            onward.append({
                **edge,
                "binding": _frozen_binding(target, target_input, catalog),
                "media_type": target_input["media_type"],
                "role": "drive",
            })
        peers = [{"id": target, "kind": nodes[target].get("kind")} for target in sorted(targets)]
        peer_edges = [edge for edge in edges if edge["source"] in targets and edge["target"] in targets]
        return onward, peers, peer_edges

    @staticmethod
    def _check_director(label: str, node_id: str, template: Mapping[str, Any], edges: Iterable[Mapping[str, Any]]) -> None:
        document = (template.get("form_data") or {}).get("video_director")
        for edge in edges:
            binding = edge.get("binding") or {}
            if binding.get("kind") != "video_director_role":
                continue
            problem = director_problem(document, binding["role"], f"{label}'s session")
            if problem:
                raise BrewRefused("director_unsupported", problem, node_id=node_id)

    def advance(self, run_id: str) -> Optional[Dict[str, Any]]:
        limits = self.limits_provider()
        with self.store.transaction() as cursor:
            run = self.store.load_run(cursor, run_id)
            if run is None or run["status"] in TERMINAL_RUN_STATUSES:
                return run
            graph = run["graph"]
            before = copy.deepcopy({"node_states": run["node_states"], "checkpoints": run["checkpoints"], "status": run["status"]})
            node_states = run["node_states"]
            checkpoints = run["checkpoints"]
            invocations = self.store.run_invocations(cursor, run_id)
            latest = latest_attempts(invocations)
            by_node: Dict[str, List[Dict[str, Any]]] = {}
            for item in latest:
                by_node.setdefault(item["node_id"], []).append(item)
            created = 0
            for node in graph["nodes"]:
                state = node_states.setdefault(node["id"], {"status": "waiting"})
                if node.get("kind") != "media":
                    status = node_status(by_node.get(node["id"], []))
                    state["status"] = status
                    if status == "done":
                        manifest = manifest_for(by_node[node["id"]], graph["ports"][node["id"]]["outputs"])
                        state["manifest"] = manifest
                        if not any(manifest.values()):
                            state["status"] = "no_output"
                        elif not state.get("result_recorded"):
                            result = {"run_id": run_id, "manifest": manifest, "done_at": utc_now()}
                            if node["id"] in graph.get("sessions", {}):
                                result["session"] = graph["sessions"][node["id"]]
                            self.store.record_result(
                                cursor,
                                run.get("elixir_id"),
                                node["id"],
                                result,
                                graph.get("descendants", {}).get(node["id"], []),
                            )
                            state["result_recorded"] = True
                    else:
                        state.pop("manifest", None)
                if state["status"] != "done":
                    continue
                for edge in graph["edges"]:
                    if edge["source"] != node["id"] or edge["id"] in checkpoints or edge.get("role") == "side":
                        continue
                    eligible = state["manifest"].get(edge["source_handle"], [])
                    checkpoint = {
                        "revision": 1,
                        "eligible": eligible,
                        "linked": linked_edge_ids(
                            [*graph["nodes"], *graph.get("peers", [])],
                            [*graph["edges"], *graph.get("peer_edges", [])],
                            edge,
                        ),
                        "draft": [asset_key(entry) for entry in eligible],
                        "confirmed": None,
                        "confirmed_at": None,
                        "source": None,
                    }
                    if run["run_mode"] == "automatic" and eligible and not run.get("cancel_requested_at"):
                        if len(eligible) > limits.max_outputs_per_connection:
                            checkpoint["error"] = (
                                f"{len(eligible)} files are ready, but one connection can carry at most "
                                f"{limits.max_outputs_per_connection}. Choose fewer files to continue."
                            )
                        else:
                            checkpoint.update(confirmed=list(checkpoint["draft"]), confirmed_at=utc_now(), source="automatic")
                    checkpoints[edge["id"]] = checkpoint
            for edge in graph["edges"]:
                checkpoint = checkpoints.get(edge["id"])
                if not checkpoint or checkpoint.get("confirmed") is None or checkpoint.get("expanded") or edge["target"] not in node_states:
                    continue
                if run.get("cancel_requested_at") or not self._sides_ready(graph, node_states, edge["target"]):
                    continue
                created += self._expand(cursor, run, edge, checkpoint)
                checkpoint["expanded"] = True
            if created:
                invocations = self.store.run_invocations(cursor, run_id)
                latest = latest_attempts(invocations)
                for node in graph["nodes"]:
                    if node.get("kind") != "media" and node_states[node["id"]]["status"] == "waiting":
                        node_states[node["id"]]["status"] = node_status([item for item in latest if item["node_id"] == node["id"]])
            status = rollup_status(run, latest, checkpoints, node_states)
            fields: Dict[str, Any] = {"node_states": node_states, "checkpoints": checkpoints, "status": status}
            if fields == before:
                return run
            if status in TERMINAL_RUN_STATUSES and not run.get("finished_at"):
                fields["finished_at"] = utc_now()
            self.store.save_run(cursor, run_id, fields)
            run.update(fields)
            return run

    @staticmethod
    def _sides_ready(graph: Mapping[str, Any], node_states: Mapping[str, Any], target: str) -> bool:
        for edge in graph["edges"]:
            if edge["target"] != target or edge.get("role") != "side":
                continue
            state = node_states.get(edge["source"])
            if state is not None and state.get("status") != "done":
                return False
        return True

    def _settle(self, run_id: str) -> Optional[Dict[str, Any]]:
        self._dirty.add(run_id)
        run = self.advance(run_id)
        self._dirty.discard(run_id)
        return run

    @staticmethod
    def _prompt_side(run: Mapping[str, Any], node_id: str) -> Optional[Mapping[str, Any]]:
        return next(
            (
                edge for edge in run["graph"]["edges"]
                if edge["target"] == node_id and edge.get("role") == "side" and edge["binding"].get("kind") == "prompt"
            ),
            None,
        )

    def _rows_for(self, run: Mapping[str, Any], edge: Mapping[str, Any], entries: List[Mapping[str, Any]]) -> List[tuple]:
        base = [(edge["id"], asset_key(entry), entry, ordinal) for ordinal, entry in enumerate(entries)]
        target = self._node(run, edge["target"])
        if target.get("kind") == "llm":
            return writer_rows(target, base)
        size = batch_size(run["templates"].get(edge["target"]) or {}, bool((run["graph"].get("prompt_batch") or {}).get(edge["target"])))
        if edge["binding"].get("kind") == "prompt":
            return [(edge["id"], key, entry, ordinal) for ordinal, (key, entry) in enumerate(text_rows(entries, size))]
        side = self._prompt_side(run, edge["target"])
        if side is None:
            return base
        texts = self._texts_of(run, side)
        return [
            (edge_id, slot, row, ordinal)
            for edge_id, key, entry, ordinal in base
            for slot, row in media_rows(key, entry, texts_for_input(texts, key), size)
        ]

    def _expand(self, cursor, run: Mapping[str, Any], edge: Mapping[str, Any], checkpoint: Mapping[str, Any]) -> int:
        by_key = {asset_key(entry): entry for entry in checkpoint["eligible"]}
        created = 0
        for _, key, entry, ordinal in self._rows_for(run, edge, [by_key[key] for key in checkpoint["confirmed"]]):
            inserted = self.store.insert_invocation(cursor, {
                "run_id": run["id"],
                "user_id": run["user_id"],
                "node_id": edge["target"],
                "edge_id": edge["id"],
                "ordinal": ordinal,
                "input_key": key,
                "input_ref": entry,
                "source_node_id": edge["source"],
                "source_run_id": run["id"],
                "source_invocation_id": entry.get("source_invocation_id"),
                "source_ordinal": entry.get("source_ordinal"),
                "source_file_index": entry.get("file_index"),
                "attempt": 1,
                "idempotency_key": invocation_key(run["id"], edge["target"], edge["id"], key, 1),
                "status": "pending",
            })
            created += 1 if inserted else 0
        return created

    def confirm_edge(self, user, run_id: str, edge_id: str, selected: Iterable[str], expected_revision: int) -> Dict[str, Any]:
        selected = list(selected)
        limits = self.limits_provider()
        with self.store.transaction() as cursor:
            run = self._owned_run(cursor, user, run_id)
            checkpoint = run["checkpoints"].get(edge_id)
            if checkpoint is None:
                raise BrewRefused("no_checkpoint", "That connection is not waiting for a choice.", status=404)
            if run.get("cancel_requested_at") or run["status"] in TERMINAL_RUN_STATUSES:
                raise BrewRefused("run_closed", "This brew is no longer running.", status=409)
            order = [asset_key(entry) for entry in checkpoint["eligible"]]
            canonical = [key for key in order if key in set(selected)]
            if checkpoint.get("confirmed") is not None:
                if checkpoint["confirmed"] == canonical:
                    return run
                raise BrewRefused("already_confirmed", "This connection was already continued with other files.", status=409)
            if checkpoint["revision"] != expected_revision:
                raise BrewRefused("revision_conflict", "The choice changed somewhere else. Reload and try again.", status=409, current_revision=checkpoint["revision"])
            if not selected:
                raise BrewRefused("empty_selection", "Choose at least one file to continue.")
            unknown = [key for key in selected if key not in order]
            if unknown or len(canonical) != len(set(selected)):
                raise BrewRefused("unknown_files", "Some chosen files are not outputs of this node.")
            if len(canonical) > limits.max_outputs_per_connection:
                raise BrewRefused("too_many_files", f"One connection can carry at most {limits.max_outputs_per_connection} files.")
            existing = len(self.store.run_invocations(cursor, run_id))
            edge = next((item for item in run["graph"]["edges"] if item["id"] == edge_id), None)
            by_key = {asset_key(entry): entry for entry in checkpoint["eligible"]}
            planned = len(self._rows_for(run, edge, [by_key[key] for key in canonical])) if edge else len(canonical)
            if existing + planned > limits.max_invocations:
                raise BrewRefused("too_many_generations", f"One brew can start at most {limits.max_invocations} generations.")
            for member in [checkpoint, *self._open_partners(run, checkpoint)]:
                member.update(
                    confirmed=canonical,
                    draft=canonical,
                    confirmed_at=utc_now(),
                    source="user",
                    revision=member["revision"] + 1,
                )
                member.pop("error", None)
            self.store.save_run(cursor, run_id, {"checkpoints": run["checkpoints"]})
        self._settle(run_id)
        self.wake()
        return self.store.get_run(run_id)

    @staticmethod
    def _open_partners(run: Mapping[str, Any], checkpoint: Mapping[str, Any]) -> List[Dict[str, Any]]:
        partners = (run["checkpoints"].get(edge_id) for edge_id in checkpoint.get("linked") or [])
        return [partner for partner in partners if partner and partner.get("confirmed") is None and partner["eligible"] == checkpoint["eligible"]]

    def save_draft(self, user, run_id: str, edge_id: str, selected: Iterable[str], expected_revision: int) -> Dict[str, Any]:
        selected = list(selected)
        with self.store.transaction() as cursor:
            run = self._owned_run(cursor, user, run_id)
            checkpoint = run["checkpoints"].get(edge_id)
            if checkpoint is None:
                raise BrewRefused("no_checkpoint", "That connection is not waiting for a choice.", status=404)
            if checkpoint.get("confirmed") is not None:
                raise BrewRefused("already_confirmed", "This connection was already continued.", status=409)
            if checkpoint["revision"] != expected_revision:
                raise BrewRefused("revision_conflict", "The choice changed somewhere else. Reload and try again.", status=409, current_revision=checkpoint["revision"])
            order = [asset_key(entry) for entry in checkpoint["eligible"]]
            if any(key not in order for key in selected):
                raise BrewRefused("unknown_files", "Some chosen files are not outputs of this node.")
            draft = [key for key in order if key in set(selected)]
            for member in [checkpoint, *self._open_partners(run, checkpoint)]:
                member.update(draft=draft, revision=member["revision"] + 1)
            self.store.save_run(cursor, run_id, {"checkpoints": run["checkpoints"]})
            return run

    def edit_text(self, user, run_id: str, text_id: str, text: str) -> Dict[str, Any]:
        with self.store.transaction() as cursor:
            run = self._owned_run(cursor, user, run_id)
            if run.get("cancel_requested_at") or run["status"] in TERMINAL_RUN_STATUSES:
                raise BrewRefused("run_closed", "This brew has finished. Pin your text to use it in the next brew.", status=409)
            items = self.store.run_invocations(cursor, run_id)
            owner = next(
                (item for item in items if any(entry.get("kind") == "text" and entry.get("text_id") == text_id for entry in item.get("outputs") or [])),
                None,
            )
            if owner is None:
                raise BrewRefused("text_not_found", "That written prompt is not part of this brew.", status=404)
            if self._text_sent(run, items, owner["node_id"]):
                raise BrewRefused("text_sent", "This prompt was already sent on. Pin your version to use it in the next brew.", status=409)

            def rewrite(entries):
                return [
                    {**entry, "text": text, "edited": True} if entry.get("kind") == "text" and entry.get("text_id") == text_id else entry
                    for entry in entries or []
                ]

            self.store.set_invocation_outputs(cursor, owner["id"], rewrite(owner["outputs"]))
            state = run["node_states"].get(owner["node_id"]) or {}
            if state.get("manifest"):
                state["manifest"] = {port: rewrite(entries) for port, entries in state["manifest"].items()}
                self.store.rewrite_result(cursor, run.get("elixir_id"), owner["node_id"], run_id, state["manifest"])
            for checkpoint in run["checkpoints"].values():
                checkpoint["eligible"] = rewrite(checkpoint.get("eligible"))
            self.store.save_run(cursor, run_id, {"node_states": run["node_states"], "checkpoints": run["checkpoints"]})
        return self.store.get_run(run_id)

    @staticmethod
    def _text_sent(run: Mapping[str, Any], items: List[Mapping[str, Any]], node_id: str) -> bool:
        for edge in run["graph"]["edges"]:
            if edge["source"] != node_id:
                continue
            if edge.get("role") == "side":
                if any(item["node_id"] == edge["target"] for item in items):
                    return True
                continue
            checkpoint = run["checkpoints"].get(edge["id"])
            if checkpoint and checkpoint.get("confirmed") is not None:
                return True
        return False

    def request_cancel(self, user, run_id: str) -> Dict[str, Any]:
        with self.store.transaction() as cursor:
            run = self._owned_run(cursor, user, run_id)
            if run["status"] in TERMINAL_RUN_STATUSES:
                return run
            if not run.get("cancel_requested_at"):
                now = utc_now()
                self.store.update_invocations_in(cursor, run_id, ("pending", "claimed"), {"status": "cancelled", "finished_at": now})
                self.store.save_run(cursor, run_id, {"cancel_requested_at": now, "status": "cancelling"})
        self._settle(run_id)
        self.wake()
        return self.store.get_run(run_id)

    def retry_failed(self, user, run_id: str, node_id: str) -> Dict[str, Any]:
        with self.store.transaction() as cursor:
            run = self._owned_run(cursor, user, run_id)
            if run.get("cancel_requested_at") or run["status"] in TERMINAL_RUN_STATUSES:
                raise BrewRefused("run_closed", "This brew is no longer running.", status=409)
            latest = [item for item in latest_attempts(self.store.run_invocations(cursor, run_id)) if item["node_id"] == node_id]
            failed = [item for item in latest if item["status"] in ("failed", "interrupted")]
            if not failed:
                raise BrewRefused("nothing_to_retry", "Nothing failed on this node.", status=409)
            limits = self.limits_provider()
            if len(self.store.run_invocations(cursor, run_id)) + len(failed) > limits.max_invocations:
                raise BrewRefused("too_many_generations", f"One brew can start at most {limits.max_invocations} generations.", node_id=node_id)
            for item in failed:
                attempt = item["attempt"] + 1
                self.store.insert_invocation(cursor, {
                    "run_id": run_id,
                    "user_id": item["user_id"],
                    "node_id": node_id,
                    "edge_id": item.get("edge_id"),
                    "ordinal": item["ordinal"],
                    "input_key": item["input_key"],
                    "input_ref": item.get("input_ref"),
                    "source_node_id": item.get("source_node_id"),
                    "source_run_id": item.get("source_run_id"),
                    "source_invocation_id": item.get("source_invocation_id"),
                    "source_ordinal": item.get("source_ordinal"),
                    "source_file_index": item.get("source_file_index"),
                    "parent_invocation_id": item["id"],
                    "attempt": attempt,
                    "idempotency_key": invocation_key(run_id, node_id, item.get("edge_id"), item["input_key"], attempt),
                    "request": self._with_key(item.get("request"), run_id, node_id, item.get("edge_id"), item["input_key"], attempt),
                    "effective_seed": item.get("effective_seed"),
                    "status": "pending",
                })
        self._settle(run_id)
        self.wake()
        return self.store.get_run(run_id)

    @staticmethod
    def _with_key(request, run_id, node_id, edge_id, input_key, attempt):
        if not request:
            return None
        return {**request, "idempotency_key": invocation_key(run_id, node_id, edge_id, input_key, attempt)}

    def _owned_run(self, cursor, user, run_id: str) -> Dict[str, Any]:
        run = self.store.load_run(cursor, run_id)
        if run is None or run["user_id"] != user.id:
            raise BrewRefused("run_not_found", "That brew does not exist.", status=404)
        return run

    async def pump(self) -> None:
        touched = await self._refresh()
        await self._advance_runs(touched)
        touched = await self._send_cancels()
        touched |= await self._dispatch()
        await self._advance_runs(touched)

    async def recover(self) -> None:
        await asyncio.to_thread(self.store.release_claims)
        self._sweep = True

    async def _advance_runs(self, touched: set) -> None:
        for run_id in await asyncio.to_thread(self._runs_to_advance, touched):
            try:
                await asyncio.to_thread(self._settle, run_id)
            except Exception:
                logger.exception("Elixir brew %s could not move on; retrying on the next pass", run_id)

    def _runs_to_advance(self, touched: set) -> List[str]:
        active = self.store.active_run_ids()
        if self._sweep:
            self._sweep = False
            return active
        wanted = touched | self._dirty | self.store.runs_with_active_invocations()
        return [run_id for run_id in active if run_id in wanted]

    async def _refresh(self) -> set:
        touched = set()
        for item in await asyncio.to_thread(self.store.invocations_with_status, ("submitted", "running")):
            try:
                if await asyncio.to_thread(self._refresh_one, item):
                    touched.add(item["run_id"])
            except Exception:
                logger.exception("Elixir invocation %s could not be checked; retrying on the next pass", item["id"])
        return touched

    def _refresh_one(self, item: Mapping[str, Any]) -> bool:
        if not item.get("generation_id"):
            return self._check_writer(item)
        user = self.port.user(item["user_id"])
        if user is None:
            return self._fail(item, "The owner of this brew can no longer run it.")
        try:
            state = self.port.state(user, item["generation_id"])
        except GenerationNotFoundException:
            return self._fail(item, "The generation was deleted.")
        status = state.get("status")
        live = ("submitted", "running")
        if status == "completed":
            request = item.get("request") or {}
            batched = bool(request.get("batch_prompts"))
            if batched and item["generation_id"] not in self._finished and item.get("submitted_boot") == self.boot_id:
                return False
            try:
                if batched:
                    files = self.port.batch_files(
                        user,
                        item["generation_id"],
                        [pair.get("positive") or "" for pair in request.get("prompts") or []],
                        per_prompt_quantity(request.get("form_data")),
                    )
                else:
                    files = self.port.files(user, item["generation_id"])
            except GenerationNotFoundException:
                files = []
            outputs = outputs_from_files(item["generation_id"], files, (item.get("input_ref") or {}).get("batch") if batched else None)
            self._finished.pop(item["generation_id"], None)
            return self.store.update_invocation(
                item["id"],
                {"status": "completed", "outputs": outputs, "finished_at": utc_now(), "error": None},
                expected_status=live,
            )
        if status == "failed":
            if item.get("submitted_boot") != self.boot_id:
                return self.store.update_invocation(
                    item["id"],
                    {"status": "interrupted", "error": "The server restarted while this generation was running.", "finished_at": utc_now()},
                    expected_status=live,
                )
            message = state.get("error_user_message") or state.get("message") or state.get("error_message") or "The generation failed."
            return self._fail(item, message)
        if status == "cancelled":
            return self.store.update_invocation(item["id"], {"status": "cancelled", "finished_at": utc_now()}, expected_status=live)
        if status == "running" and item["status"] == "submitted":
            return self.store.update_invocation(item["id"], {"status": "running"}, expected_status="submitted")
        return False

    def _check_writer(self, item: Mapping[str, Any]) -> bool:
        if item["status"] != "running" or item["id"] in self._writers:
            return False
        message = (
            "The server restarted while this prompt was being written."
            if item.get("submitted_boot") != self.boot_id
            else "Writing this prompt stopped unexpectedly."
        )
        return self.store.update_invocation(
            item["id"],
            {"status": "interrupted", "error": message, "finished_at": utc_now()},
            expected_status="running",
        )

    def _fail(self, item: Mapping[str, Any], message: str) -> bool:
        return self.store.update_invocation(
            item["id"],
            {"status": "failed", "error": message, "finished_at": utc_now()},
            expected_status=ACTIVE_INVOCATION_STATUSES,
        )

    def _cancel_targets(self) -> List[tuple]:
        targets = []
        for run_id in self.store.cancelling_run_ids():
            run = self.store.get_run(run_id)
            if not run:
                continue
            user = self.port.user(run["user_id"])
            for item in self.store.invocations(run_id):
                if item["status"] in ("submitted", "running") and (item.get("generation_id") or item["id"] in self._writers):
                    targets.append((user, item))
        return targets

    async def _send_cancels(self) -> set:
        touched = set()
        targets = await asyncio.to_thread(self._cancel_targets)
        self._cancel_sent &= {item["generation_id"] for _, item in targets}
        for user, item in targets:
            if not item.get("generation_id"):
                task = self._writers.get(item["id"])
                if task is not None and not task.done() and item["id"] not in self._stopping:
                    self._stopping.add(item["id"])
                    task.cancel()
                touched.add(item["run_id"])
                continue
            generation_id = item["generation_id"]
            if generation_id in self._cancel_sent:
                continue
            try:
                if user is not None:
                    try:
                        sent = await self.port.cancel(user, generation_id)
                    except GenerationNotFoundException:
                        self._cancel_sent.add(generation_id)
                        await asyncio.to_thread(
                            self.store.update_invocation,
                            item["id"],
                            {"status": "cancelled", "finished_at": utc_now()},
                            ("submitted", "running"),
                        )
                        touched.add(item["run_id"])
                        continue
                    if sent:
                        self._cancel_sent.add(generation_id)
                await asyncio.to_thread(self._refresh_one, item)
                touched.add(item["run_id"])
            except Exception:
                logger.exception("Elixir generation %s could not be stopped; retrying on the next pass", generation_id)
        return touched

    async def _dispatch(self) -> set:
        touched = set()
        for item in await asyncio.to_thread(self.store.invocations_with_status, ("pending",)):
            try:
                run, claimed = await asyncio.to_thread(self._claim, item)
                if run is None:
                    continue
                touched.add(item["run_id"])
                if claimed:
                    await self._submit(run, item)
            except Exception:
                logger.exception("Elixir invocation %s could not be started; retrying on the next pass", item["id"])
        return touched

    def _claim(self, item: Mapping[str, Any]):
        run = self.store.get_run(item["run_id"])
        if run is None:
            return None, False
        if run.get("cancel_requested_at"):
            cancelled = self.store.update_invocation(item["id"], {"status": "cancelled", "finished_at": utc_now()}, expected_status="pending")
            return (run if cancelled else None), False
        if run["status"] not in ACTIVE_RUN_STATUSES:
            return None, False
        if not self.store.update_invocation(item["id"], {"status": "claimed", "claimed_at": utc_now()}, expected_status="pending"):
            return None, False
        return run, True

    def _prepare(self, run: Mapping[str, Any], item: Mapping[str, Any]):
        user = self.port.user(run["user_id"])
        if user is None:
            raise BindingFailed("The owner of this brew can no longer run it.")
        request = item.get("request")
        if not request:
            request, seed = self._build_request(user, run, item)
            self.store.update_invocation(item["id"], {"request": request, "effective_seed": seed}, expected_status="claimed")
        return user, request

    @staticmethod
    def _node(run: Mapping[str, Any], node_id: str) -> Mapping[str, Any]:
        return next((node for node in run["graph"]["nodes"] if node["id"] == node_id), {"id": node_id})

    async def _submit(self, run: Mapping[str, Any], item: Mapping[str, Any]) -> None:
        if self._node(run, item["node_id"]).get("kind") == "llm":
            await self._start_writing(run, item)
            return
        try:
            user, request = await asyncio.to_thread(self._prepare, run, item)
        except BindingFailed as error:
            await asyncio.to_thread(self._fail, item, str(error))
            return
        except Exception:
            logger.exception("Elixir invocation %s could not be prepared", item["id"])
            await asyncio.to_thread(self._fail, item, "The generation could not be prepared.")
            return
        try:
            submitted = await self.port.submit(user, request)
        except Exception:
            logger.exception("Elixir invocation %s could not be submitted", item["id"])
            await asyncio.to_thread(self._fail, item, "The generation could not be started.")
            return
        generation_id = submitted["generation_id"]
        recorded = await asyncio.to_thread(
            self.store.update_invocation,
            item["id"],
            {"status": "submitted", "generation_id": generation_id, "submitted_at": utc_now(), "submitted_boot": self.boot_id},
            "claimed",
        )
        if recorded:
            return
        await asyncio.to_thread(self.store.update_invocation, item["id"], {"generation_id": generation_id}, "cancelled")
        try:
            await self.port.cancel(user, generation_id)
        except Exception:
            logger.exception("Elixir generation %s was started after its brew stopped and could not be stopped", generation_id)

    async def _start_writing(self, run: Mapping[str, Any], item: Mapping[str, Any]) -> None:
        try:
            user, request = await asyncio.to_thread(self._prepare, run, item)
        except BindingFailed as error:
            await asyncio.to_thread(self._fail, item, str(error))
            return
        except Exception:
            logger.exception("Elixir prompt %s could not be prepared", item["id"])
            await asyncio.to_thread(self._fail, item, "The prompt could not be prepared.")
            return
        now = utc_now()
        if request.get("pinned"):
            await asyncio.to_thread(
                self.store.update_invocation,
                item["id"],
                {
                    "status": "completed",
                    "outputs": text_outputs(
                        uuid.uuid4().hex, request["pinned"], request["input_key"], pinned=True,
                        slot_key=request.get("slot_key"), prompt_count=request.get("prompt_count") or 1,
                    ),
                    "submitted_at": now,
                    "finished_at": now,
                    "error": None,
                },
                "claimed",
            )
            return
        recorded = await asyncio.to_thread(
            self.store.update_invocation,
            item["id"],
            {"status": "running", "submitted_at": now, "submitted_boot": self.boot_id},
            "claimed",
        )
        if not recorded:
            return
        task = asyncio.create_task(self._write(user, item["run_id"], item["id"], request))
        self._writers[item["id"]] = task
        task.add_done_callback(lambda _, key=item["id"]: self._writers.pop(key, None))

    async def _write(self, user, run_id: str, invocation_id: str, request: Mapping[str, Any]) -> None:
        try:
            result = await self.port.write_prompt(user, request)
            text = str((result or {}).get("text") or "").strip()[:MAX_WRITTEN_CHARS]
            if text:
                fields = {"status": "completed", "outputs": text_outputs(
                    uuid.uuid4().hex, text, request["input_key"], reasoning=_reasoning(result),
                    instruction=request.get("rolled_instruction"), instruction_seed=request.get("instruction_seed"),
                    slot_key=request.get("slot_key"), prompt_count=request.get("prompt_count") or 1,
                ), "error": None}
            else:
                fields = {"status": "failed", "error": "The language model returned no prompt."}
        except asyncio.CancelledError:
            if invocation_id in self._stopping:
                self._stopping.discard(invocation_id)
                await asyncio.to_thread(
                    self.store.update_invocation, invocation_id, {"status": "cancelled", "finished_at": utc_now()}, "running",
                )
                self._dirty.add(run_id)
                self.wake()
            raise
        except WriteRefused as refused:
            fields = {"status": "failed", "error": _short(str(refused))}
        except Exception as error:
            logger.warning("Elixir prompt %s could not be written: %s", invocation_id, error)
            fields = {"status": "failed", "error": _short(f"The language model could not write a prompt: {error}")}
        await asyncio.to_thread(self.store.update_invocation, invocation_id, {**fields, "finished_at": utc_now()}, "running")
        self._dirty.add(run_id)
        self.wake()

    def writer_tasks(self) -> List[asyncio.Task]:
        return list(self._writers.values())

    async def wait_for_writers(self) -> None:
        while self._writers:
            await asyncio.gather(*list(self._writers.values()), return_exceptions=True)
            await asyncio.sleep(0)

    def _writer_request(self, user, run: Mapping[str, Any], node: Mapping[str, Any], item: Mapping[str, Any]) -> Dict[str, Any]:
        target = (run["graph"].get("targets") or {}).get(node["id"]) or {}
        template = run["templates"].get(target.get("node_id")) or {}
        request: Dict[str, Any] = {
            "kind": "llm",
            "instruction": node.get("instruction") or "",
            "llm_id": node.get("llm_id"),
            "input_key": slot_parts(item["input_key"])[0],
            "preset_id": target.get("preset_id"),
            "mode": target.get("mode"),
            "form_data": template.get("form_data") or {},
            "media": [],
        }
        if prompts_per_input(node) > 1:
            request.update(slot_key=item["input_key"], prompt_count=prompts_per_input(node))
        pinned = (node.get("pinned") or {}).get(item["input_key"])
        if pinned:
            request["pinned"] = pinned
            return request
        rolled = roll_instruction(
            request["instruction"],
            node.get("instruction_chips"),
            node.get("variables"),
            self.random_below(SEED_LIMIT),
            lambda path: self.port.phrasebook_texts(user, path),
        )
        if rolled.rolled:
            request["instruction"] = rolled.text
            request["instruction_seed"] = rolled.seed
            request["rolled_instruction"] = rolled.text
        if item.get("edge_id"):
            try:
                resolved = self.port.resolve(user, media_ref(item["input_ref"]))
            except MediaRefError:
                raise BindingFailed(f"File {item['ordinal'] + 1} is no longer available.") from None
            request["media"] = [{"value": resolved["value"], "media_type": str(resolved.get("media_type") or "").lower()}]
        return request

    @staticmethod
    def _texts_of(run: Mapping[str, Any], edge: Mapping[str, Any]) -> List[Dict[str, Any]]:
        state = run["node_states"].get(edge["source"])
        if state is not None and state.get("manifest"):
            return state["manifest"].get(WRITER_OUTPUT) or []
        return (run.get("pinned_inputs") or {}).get("sides", {}).get(edge["id"], [])

    def _build_request(self, user, run: Mapping[str, Any], item: Mapping[str, Any]):
        node = self._node(run, item["node_id"])
        if node.get("kind") == "llm":
            request = self._writer_request(user, run, node, item)
            return request, request.get("instruction_seed")
        request = copy.deepcopy(run["templates"][item["node_id"]])
        batch = (item.get("input_ref") or {}).get("batch")
        if item.get("edge_id"):
            edge = next(edge for edge in run["graph"]["edges"] if edge["id"] == item["edge_id"])
            kind = edge["binding"].get("kind")
            if kind in TEXT_BINDINGS and batch:
                request = bind_texts(request, self._batch_texts(self._texts_of(run, edge), batch))
            elif kind in TEXT_BINDINGS:
                entry = item.get("input_ref") or {}
                current = next((text for text in self._texts_of(run, edge) if text.get("text_id") == entry.get("text_id")), entry)
                request = bind_text(request, kind, current.get("text") or "")
            else:
                try:
                    resolved = self.port.resolve(user, media_ref(item["input_ref"]))
                except MediaRefError:
                    raise BindingFailed(f"File {item['ordinal'] + 1} is no longer available.") from None
                request = bind_input(request, edge["binding"], resolved, edge.get("target_segment_id"), self.port.bind_director)
        for side in run["graph"]["edges"]:
            if side["target"] != item["node_id"] or side.get("role") != "side":
                continue
            texts = self._texts_of(run, side)
            if batch and side["binding"]["kind"] == "prompt":
                request = bind_texts(request, self._batch_texts(texts, batch))
                continue
            chosen = pick_text(texts, slot_parts(item["input_key"])[0])
            if chosen is None:
                if not texts:
                    raise BindingFailed("No written prompt reached this node.")
                raise BindingFailed(f"None of the {len(texts)} written prompts was written for file {item['ordinal'] + 1}.")
            request = bind_text(request, side["binding"]["kind"], chosen.get("text") or "")
        seed = resolve_seed(request, self.random_below)
        request["idempotency_key"] = item["idempotency_key"]
        return request, seed

    @staticmethod
    def _batch_texts(texts: List[Mapping[str, Any]], keys: List[str]) -> List[str]:
        by_key = {asset_key(text): text for text in texts}
        missing = [key for key in keys if key not in by_key]
        if missing:
            raise BindingFailed(f"{len(missing)} of the {len(keys)} written prompts for this generation are no longer available.")
        return [by_key[key].get("text") or "" for key in keys]

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._queue = asyncio.Queue()

    def wake(self) -> None:
        if self._loop is None or self._queue is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
        except RuntimeError:
            logger.warning("Elixir coordinator loop is closed; the next poll will pick this up")

    def on_generation_finished(self, generation_id: Optional[str]) -> None:
        if generation_id:
            self._finished[generation_id] = None
            while len(self._finished) > MAX_FINISHED_REMEMBERED:
                self._finished.pop(next(iter(self._finished)))
            self.wake()

    async def run_forever(self, poll_seconds: float = 15.0) -> None:
        if self._queue is None:
            self.attach(asyncio.get_running_loop())
        recovered = False
        while True:
            try:
                if not recovered:
                    await self.recover()
                    recovered = True
                await self.pump()
            except Exception:
                logger.exception("Elixir coordinator pass failed; retrying on the next wake or poll")
            await self._next_wake(poll_seconds)

    async def _next_wake(self, poll_seconds: float) -> None:
        try:
            await asyncio.wait_for(self._queue.get(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            return
        while not self._queue.empty():
            self._queue.get_nowait()
