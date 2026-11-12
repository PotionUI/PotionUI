from typing import TYPE_CHECKING, Any, Callable, Dict, NoReturn, Optional

from fastapi import HTTPException

from src.features.elixirs.coordinator import BrewRefused, Coordinator
from src.features.elixirs.follow import SESSION_CHANGED_MESSAGE, followed_issues, read_followed
from src.features.elixirs.graph import validate_graph
from src.features.elixirs.media import unavailable_assets
from src.features.elixirs.records import ElixirRecord
from src.features.elixirs.repository import ElixirRepository
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.features.elixirs.components import ElixirsComponents


def writer_for(nodes, status: Callable[[User, Optional[str]], Dict[str, Any]], user: User) -> Optional[Callable[[Optional[str]], Dict[str, Any]]]:
    if not any(node.get("kind") == "llm" for node in nodes):
        return None
    known: Dict[Optional[str], Dict[str, Any]] = {}

    def lookup(llm_id: Optional[str] = None) -> Dict[str, Any]:
        if llm_id not in known:
            known[llm_id] = status(user, llm_id)
        return known[llm_id]

    return lookup


def fail(status: int, code: str, message: str, **extra: Any) -> NoReturn:
    raise HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def owned(repository: ElixirRepository, elixir_id: str, user: User) -> ElixirRecord:
    record = repository.get(elixir_id, user.id)
    if record is None:
        fail(404, "elixir_not_found", "That elixir does not exist.")
    return record


def with_followed(report: Dict[str, Any], nodes, states: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    issues = report["issues"] + followed_issues(nodes, states)
    return {**report, "issues": issues, "valid": not any(issue["severity"] == "error" for issue in issues)}


def session_pins(nodes, node_ids, states: Dict[str, Dict[str, Any]], hashes: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    pins: Dict[str, Dict[str, Any]] = {}
    for node_id in node_ids:
        state = states.get(node_id)
        if state is None:
            continue
        if state["status"] != "ok":
            fail(422, "node_not_ready", "Fix this node's problems before running it.", issues=followed_issues(nodes, {node_id: state}), node_id=node_id)
        if hashes.get(node_id) != state["session_hash"]:
            fail(409, "session_changed", SESSION_CHANGED_MESSAGE, node_id=node_id)
        pins[node_id] = {"session_id": state["session"]["id"], "version": state["session"]["version"], "session_hash": state["session_hash"]}
    return pins


def refused(error: BrewRefused) -> NoReturn:
    raise HTTPException(status_code=error.status, detail=error.to_detail())


def progress_of(coordinator: Coordinator, user: User, invocation: Dict[str, Any]) -> Optional[float]:
    if invocation["status"] not in ("submitted", "running") or not invocation.get("generation_id"):
        return None
    try:
        state = coordinator.port.state(user, invocation["generation_id"])
    except Exception:
        return None
    progress = state.get("progress")
    return float(progress) if isinstance(progress, (int, float)) else None


def run_detail(coordinator: Coordinator, user: User, run: Dict[str, Any]) -> Dict[str, Any]:
    inherited = coordinator.store.inherited_from_results(run)
    invocations = [*coordinator.store.invocations(run["id"]), *inherited["invocations"]]
    return {
        "id": run["id"],
        "elixir_id": run["elixir_id"],
        "elixir_revision": run["elixir_revision"],
        "scope": run["scope"],
        "run_mode": run["run_mode"],
        "status": run["status"],
        "error": run.get("error"),
        "cancel_requested_at": run.get("cancel_requested_at"),
        "created_at": run["created_at"],
        "updated_at": run["updated_at"],
        "finished_at": run.get("finished_at"),
        "node_states": {**inherited["node_states"], **run["node_states"]},
        "checkpoints": {**inherited["checkpoints"], **run["checkpoints"]},
        "graph_edges": [
            {key: edge.get(key) for key in ("id", "source", "source_handle", "target", "target_handle")}
            for edge in [*run["graph"].get("edges", []), *inherited["edges"]]
        ],
        "scope_node_id": run.get("scope_node_id"),
        "invocations": [
            {
                "id": item["id"],
                "node_id": item["node_id"],
                "edge_id": item.get("edge_id"),
                "ordinal": item["ordinal"],
                "input_key": item["input_key"],
                "input_ref": item.get("input_ref"),
                "attempt": item["attempt"],
                "status": item["status"],
                "error": item.get("error"),
                "generation_id": item.get("generation_id"),
                "effective_seed": item.get("effective_seed"),
                "outputs": item.get("outputs") or [],
                "progress": progress_of(coordinator, user, item),
                "submitted_at": item.get("submitted_at"),
                "finished_at": item.get("finished_at"),
            }
            for item in invocations
        ],
    }


def begin_brew(
    elixirs: "ElixirsComponents",
    user: User,
    elixir_id: str,
    expected_revision: int,
    requested_run_mode: Optional[str],
    templates: Dict[str, Dict[str, Any]],
    session_hashes: Optional[Dict[str, str]],
    idempotency_key: str,
) -> Dict[str, Any]:
    repository = elixirs.repository
    resolver = elixirs.resolver
    catalog_lookup = elixirs.catalog
    contracts = elixirs.contracts()
    limits = elixirs.limits()
    coordinator = elixirs.coordinator
    writer_status = elixirs.coordinator.port.writer_status
    read_session = elixirs.sessions.get_session
    record = owned(repository, elixir_id, user)
    if record.revision != expected_revision:
        fail(409, "revision_conflict", "This elixir changed since you opened it. Reload it and brew again.", current_revision=record.revision)
    run_mode = requested_run_mode or record.run_mode
    nodes = record.definition.get("nodes") or []
    catalog = catalog_lookup(node.get("preset_id") for node in nodes if node.get("kind") == "preset")
    states = read_followed(user, nodes, read_session)
    report = with_followed(validate_graph(
        record.definition,
        run_mode=run_mode,
        catalog=catalog,
        contracts=contracts,
        limits=limits,
        unavailable=unavailable_assets(user, nodes, resolver),
        writer=writer_for(nodes, writer_status, user),
    ), nodes, states)
    if not report["valid"]:
        fail(422, "elixir_not_ready", "Fix the problems on the canvas before brewing.", issues=report["issues"])
    if not nodes:
        fail(422, "elixir_empty", "Add a node before brewing.")
    if session_hashes is None:
        session_hashes = {node_id: state["session_hash"] for node_id, state in states.items() if state["status"] == "ok"}
    pins = session_pins(nodes, [node["id"] for node in nodes], states, session_hashes)
    try:
        run = coordinator.start_graph_run(
            user,
            {"id": record.id, "revision": record.revision, "definition": record.definition},
            contracts,
            templates,
            run_mode,
            idempotency_key,
            catalog=catalog,
            sessions=pins,
        )
    except BrewRefused as error:
        refused(error)
    return run_detail(coordinator, user, run)
