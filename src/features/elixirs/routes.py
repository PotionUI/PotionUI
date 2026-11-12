import re
from datetime import date
from typing import TYPE_CHECKING, Any, Callable, Dict, Literal, NoReturn, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.features.elixirs.components import ElixirsComponents
from src.features.elixirs.contracts import lint_contract
from src.features.elixirs.coordinator import BrewRefused, Coordinator
from src.features.elixirs.definitions import (
    NodeRejected,
    copy_followed,
    follow_session,
    import_session,
    merge_definition,
    own_settings,
)
from src.features.elixirs.dto import (
    CancelBrewsRequest,
    CreateElixirRequest,
    EditTextRequest,
    FollowSessionRequest,
    ImportSessionRequest,
    NodeRevisionRequest,
    NodeRunRequest,
    SelectionRequest,
    StartBrewRequest,
    UpdateElixirRequest,
)
from src.features.elixirs.brewing import (
    begin_brew,
    fail,
    owned,
    refused,
    run_detail,
    session_pins,
    with_followed,
    writer_for,
)
from src.features.elixirs.errors import SessionNotFoundError
from src.features.elixirs.follow import (
    is_following,
    read_followed,
)
from src.features.elixirs.graph import validate_graph, writer_target
from src.features.elixirs.listing import summaries
from src.features.elixirs.media import unavailable_assets
from src.features.elixirs.records import (
    ElixirHasActiveRuns,
    RevisionConflict,
)
from src.features.elixirs.repository import ElixirRepository
from src.platform.security.api_scopes import ApiScope, key_access, key_scope
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer



DEFAULT_BREW_PAGE = 50
MAX_BREW_PAGE = 200
DAY_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


def _is_day(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return bool(DAY_PATTERN.fullmatch(value))



def _rejected(error: NodeRejected) -> NoReturn:
    raise HTTPException(status_code=error.status, detail=error.to_detail())


def _conflict(error: RevisionConflict) -> NoReturn:
    fail(
        409,
        "revision_conflict",
        "This elixir was changed somewhere else. Reload it and try again.",
        current_revision=error.current_revision,
    )


def _save_node(repository: ElixirRepository, elixir_id: str, user: User, expected_revision: int, node_id: str, change: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    try:
        definition = change()
    except NodeRejected as error:
        _rejected(error)
    try:
        record = repository.update(elixir_id, user.id, expected_revision, definition=definition)
    except RevisionConflict as error:
        _conflict(error)
    if record is None:
        fail(404, "elixir_not_found", "That elixir does not exist.")
    full = record.to_full()
    return {**full, "node": next(node for node in full["definition"]["nodes"] if node["id"] == node_id)}


def _current_session(read_session: Callable[..., Dict[str, Any]], user: User, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not session_id:
        return None
    try:
        return read_session(user, session_id)
    except SessionNotFoundError:
        return None



def _owned_run(coordinator: Coordinator, run_id: str, user: User) -> Dict[str, Any]:
    run = coordinator.store.get_run(run_id, user.id)
    if run is None:
        fail(404, "run_not_found", "That brew does not exist.")
    return run



def build_router(container: "AppContainer") -> APIRouter:
    return create_router(container.elixirs)


def create_router(elixirs: ElixirsComponents) -> APIRouter:
    router = APIRouter(prefix="/api/elixirs", tags=["Elixirs"], dependencies=[key_access(read=ApiScope.READ, write=ApiScope.CONTENT)])

    @router.get("/presets")
    def list_presets(
        user: User = Depends(get_current_active_user),
    ):
        catalog_lookup = elixirs.catalog
        contracts = elixirs.contracts()
        catalog = catalog_lookup(preset_id for preset_id, _ in contracts)
        usable = []
        for (preset_id, mode), contract in contracts.items():
            preset = catalog.get(preset_id)
            if preset is None or lint_contract(preset_id, mode, contract, catalog):
                continue
            usable.append({
                "preset_id": preset_id,
                "preset_name": preset["name"],
                "mode": mode,
                "mode_label": contract.label,
                "prompt": contract.prompt,
                "inputs": {
                    name: {"label": port.label, "media_type": port.media_type, "target": port.target.kind, "role": port.target.role}
                    for name, port in contract.inputs.items()
                },
                "outputs": {
                    name: {"label": port.label, "media_type": port.media_type}
                    for name, port in contract.outputs.items()
                },
            })
        usable.sort(key=lambda item: (item["preset_name"].lower(), item["mode_label"].lower()))
        return {"presets": usable}

    @router.get("")
    def list_elixirs(
        user: User = Depends(get_current_active_user),
    ):
        repository = elixirs.repository
        records = repository.list(user.id)
        extras = summaries(repository, records, user.id)
        return {"elixirs": [{**record.to_summary(), **extras[record.id]} for record in records]}

    @router.post("", status_code=201)
    def create_elixir(
        body: CreateElixirRequest,
        user: User = Depends(get_current_active_user),
    ):
        repository = elixirs.repository
        resolver = elixirs.resolver
        try:
            definition = merge_definition(body.definition, None, user, resolver)
        except NodeRejected as error:
            _rejected(error)
        return repository.create(user.id, body.name, body.run_mode, definition).to_full()

    @router.get("/runs/{run_id}")
    def get_brew(
        run_id: str,
        user: User = Depends(get_current_active_user),
    ):
        coordinator = elixirs.coordinator
        return run_detail(coordinator, user, _owned_run(coordinator, run_id, user))

    @router.put("/runs/{run_id}/edges/{edge_id}/selection", dependencies=[key_scope(ApiScope.GENERATE)])
    def save_selection(
        run_id: str,
        edge_id: str,
        body: SelectionRequest,
        user: User = Depends(get_current_active_user),
    ):
        coordinator = elixirs.coordinator
        _owned_run(coordinator, run_id, user)
        try:
            coordinator.save_draft(user, run_id, edge_id, body.selected, body.expected_revision)
        except BrewRefused as error:
            refused(error)
        return run_detail(coordinator, user, _owned_run(coordinator, run_id, user))

    @router.post("/runs/{run_id}/edges/{edge_id}/continue", dependencies=[key_scope(ApiScope.GENERATE)])
    def continue_edge(
        run_id: str,
        edge_id: str,
        body: SelectionRequest,
        user: User = Depends(get_current_active_user),
    ):
        coordinator = elixirs.coordinator
        _owned_run(coordinator, run_id, user)
        try:
            run = coordinator.confirm_edge(user, run_id, edge_id, body.selected, body.expected_revision)
        except BrewRefused as error:
            refused(error)
        return run_detail(coordinator, user, run)

    @router.put("/runs/{run_id}/texts/{text_id}", dependencies=[key_scope(ApiScope.GENERATE)])
    def edit_text(
        run_id: str,
        text_id: str,
        body: EditTextRequest,
        user: User = Depends(get_current_active_user),
    ):
        coordinator = elixirs.coordinator
        _owned_run(coordinator, run_id, user)
        try:
            run = coordinator.edit_text(user, run_id, text_id, body.text)
        except BrewRefused as error:
            refused(error)
        return run_detail(coordinator, user, run)

    @router.post("/runs/{run_id}/cancel", dependencies=[key_scope(ApiScope.GENERATE)])
    def cancel_brew(
        run_id: str,
        user: User = Depends(get_current_active_user),
    ):
        coordinator = elixirs.coordinator
        _owned_run(coordinator, run_id, user)
        try:
            run = coordinator.request_cancel(user, run_id)
        except BrewRefused as error:
            refused(error)
        return run_detail(coordinator, user, run)

    @router.post("/runs/{run_id}/nodes/{node_id}/retry-failed", dependencies=[key_scope(ApiScope.GENERATE)])
    def retry_failed(
        run_id: str,
        node_id: str,
        user: User = Depends(get_current_active_user),
    ):
        coordinator = elixirs.coordinator
        _owned_run(coordinator, run_id, user)
        try:
            run = coordinator.retry_failed(user, run_id, node_id)
        except BrewRefused as error:
            refused(error)
        return run_detail(coordinator, user, run)

    @router.get("/{elixir_id}")
    def get_elixir(
        elixir_id: str,
        user: User = Depends(get_current_active_user),
    ):
        repository = elixirs.repository
        return owned(repository, elixir_id, user).to_full()

    @router.put("/{elixir_id}")
    def update_elixir(
        elixir_id: str,
        body: UpdateElixirRequest,
        user: User = Depends(get_current_active_user),
    ):
        repository = elixirs.repository
        resolver = elixirs.resolver
        stored = owned(repository, elixir_id, user)
        definition = None
        if body.definition is not None:
            try:
                definition = merge_definition(body.definition, stored.definition, user, resolver)
            except NodeRejected as error:
                _rejected(error)
        try:
            record = repository.update(
                elixir_id,
                user.id,
                body.expected_revision,
                name=body.name,
                run_mode=body.run_mode,
                definition=definition,
            )
        except RevisionConflict as error:
            _conflict(error)
        if record is None:
            fail(404, "elixir_not_found", "That elixir does not exist.")
        return record.to_full()

    @router.delete("/{elixir_id}")
    def delete_elixir(
        elixir_id: str,
        user: User = Depends(get_current_active_user),
    ):
        repository = elixirs.repository
        try:
            deleted = repository.delete(elixir_id, user.id)
        except ElixirHasActiveRuns:
            fail(409, "elixir_is_brewing", "This elixir is still brewing. Stop the brew before deleting it.")
        if not deleted:
            fail(404, "elixir_not_found", "That elixir does not exist.")
        return {"deleted": True, "id": elixir_id}

    @router.post("/{elixir_id}/validate", dependencies=[key_scope(ApiScope.READ)])
    def validate_elixir(
        elixir_id: str,
        user: User = Depends(get_current_active_user),
    ):
        repository = elixirs.repository
        resolver = elixirs.resolver
        catalog_lookup = elixirs.catalog
        contracts = elixirs.contracts()
        limits = elixirs.limits()
        writer_status = elixirs.coordinator.port.writer_status
        read_session = elixirs.sessions.get_session
        record = owned(repository, elixir_id, user)
        nodes = record.definition.get("nodes") or []
        catalog = catalog_lookup(node.get("preset_id") for node in nodes if node.get("kind") == "preset")
        report = validate_graph(
            record.definition,
            run_mode=record.run_mode,
            catalog=catalog,
            contracts=contracts,
            limits=limits,
            unavailable=unavailable_assets(user, nodes, resolver),
            writer=writer_for(nodes, writer_status, user),
        )
        return {"revision": record.revision, **with_followed(report, nodes, read_followed(user, nodes, read_session))}

    @router.get("/{elixir_id}/followed-sessions")
    def followed_sessions(
        elixir_id: str,
        user: User = Depends(get_current_active_user),
    ):
        repository = elixirs.repository
        read_session = elixirs.sessions.get_session
        record = owned(repository, elixir_id, user)
        return {"revision": record.revision, "nodes": read_followed(user, record.definition.get("nodes") or [], read_session)}

    @router.post("/{elixir_id}/nodes/{node_id}/session")
    def import_node_session(
        elixir_id: str,
        node_id: str,
        body: ImportSessionRequest,
        user: User = Depends(get_current_active_user),
    ):
        repository = elixirs.repository
        read_snapshot = elixirs.sessions.get_snapshot
        stored = owned(repository, elixir_id, user)
        try:
            snapshot = read_snapshot(user, body.session_id, body.version)
        except SessionNotFoundError:
            fail(404, "session_not_found", "That saved session or version does not exist.")
        try:
            definition = import_session(stored.definition, node_id, snapshot, body.mode, body.overrides)
        except NodeRejected as error:
            _rejected(error)
        try:
            record = repository.update(elixir_id, user.id, body.expected_revision, definition=definition)
        except RevisionConflict as error:
            _conflict(error)
        if record is None:
            fail(404, "elixir_not_found", "That elixir does not exist.")
        return record.to_full()

    @router.post("/{elixir_id}/nodes/{node_id}/session/follow")
    def follow_node_session(
        elixir_id: str,
        node_id: str,
        body: FollowSessionRequest,
        user: User = Depends(get_current_active_user),
    ):
        repository = elixirs.repository
        read_session = elixirs.sessions.get_session
        stored = owned(repository, elixir_id, user)
        current = _current_session(read_session, user, body.session_id)
        if current is None:
            fail(404, "session_not_found", "That saved session does not exist.")
        return _save_node(
            repository, elixir_id, user, body.expected_revision, node_id,
            lambda: follow_session(stored.definition, node_id, current, body.mode),
        )

    @router.post("/{elixir_id}/nodes/{node_id}/session/copy")
    def copy_node_session(
        elixir_id: str,
        node_id: str,
        body: NodeRevisionRequest,
        user: User = Depends(get_current_active_user),
    ):
        repository = elixirs.repository
        read_session = elixirs.sessions.get_session
        stored = owned(repository, elixir_id, user)
        node = next((item for item in stored.definition.get("nodes") or [] if item["id"] == node_id), None)
        current = _current_session(read_session, user, node["session_ref"]["id"]) if node and is_following(node) else None
        return _save_node(
            repository, elixir_id, user, body.expected_revision, node_id,
            lambda: copy_followed(stored.definition, node_id, current),
        )

    @router.post("/{elixir_id}/nodes/{node_id}/session/new")
    def own_node_settings(
        elixir_id: str,
        node_id: str,
        body: NodeRevisionRequest,
        user: User = Depends(get_current_active_user),
    ):
        repository = elixirs.repository
        stored = owned(repository, elixir_id, user)
        return _save_node(
            repository, elixir_id, user, body.expected_revision, node_id,
            lambda: own_settings(stored.definition, node_id),
        )

    @router.post("/{elixir_id}/runs", status_code=201, dependencies=[key_scope(ApiScope.GENERATE)])
    def start_brew(
        elixir_id: str,
        body: StartBrewRequest,
        user: User = Depends(get_current_active_user),
    ):
        return begin_brew(
            elixirs, user, elixir_id, body.expected_revision, body.run_mode, body.templates,
            body.session_hashes, body.idempotency_key,
        )

    @router.get("/{elixir_id}/runs")
    def list_brews(
        elixir_id: str,
        status: Literal["all", "active", "finished", "failed", "cancelled"] = "all",
        date_from: Optional[str] = Query(None, alias="from"),
        date_to: Optional[str] = Query(None, alias="to"),
        run_mode: Optional[Literal["review", "automatic"]] = None,
        scope: Optional[Literal["elixir", "node"]] = None,
        q: Optional[str] = Query(None, max_length=200),
        limit: int = Query(DEFAULT_BREW_PAGE, ge=1, le=MAX_BREW_PAGE),
        offset: int = Query(0, ge=0),
        user: User = Depends(get_current_active_user),
    ):
        repository = elixirs.repository
        coordinator = elixirs.coordinator
        owned(repository, elixir_id, user)
        for value in (date_from, date_to):
            if value and not _is_day(value):
                fail(422, "invalid_date", "Dates must look like YYYY-MM-DD.")
        page = coordinator.store.page_runs(
            elixir_id,
            user.id,
            status=status,
            date_from=date_from,
            date_to=date_to,
            run_mode=run_mode,
            scope=scope,
            query=q,
            limit=limit,
            offset=offset,
        )
        return {**page, "limit": limit, "offset": offset}

    @router.post("/{elixir_id}/runs/cancel", dependencies=[key_scope(ApiScope.GENERATE)])
    def cancel_brews(
        elixir_id: str,
        body: CancelBrewsRequest,
        user: User = Depends(get_current_active_user),
    ):
        repository = elixirs.repository
        coordinator = elixirs.coordinator
        owned(repository, elixir_id, user)
        if body.run_ids is None and not body.all_active:
            fail(422, "nothing_to_cancel", "Choose brews to stop, or stop all active brews.")
        wanted = None if body.all_active else list(dict.fromkeys(body.run_ids or []))
        cancelled = []
        for run_id in coordinator.store.cancellable_run_ids(elixir_id, user.id, wanted):
            try:
                run = coordinator.request_cancel(user, run_id)
            except BrewRefused:
                continue
            if run.get("cancel_requested_at"):
                cancelled.append(run_id)
        return {"cancelled": cancelled}

    @router.post("/{elixir_id}/nodes/{node_id}/runs", status_code=201, dependencies=[key_scope(ApiScope.GENERATE)])
    def run_node(
        elixir_id: str,
        node_id: str,
        body: NodeRunRequest,
        user: User = Depends(get_current_active_user),
    ):
        repository = elixirs.repository
        resolver = elixirs.resolver
        catalog_lookup = elixirs.catalog
        contracts = elixirs.contracts()
        limits = elixirs.limits()
        coordinator = elixirs.coordinator
        writer_status = elixirs.coordinator.port.writer_status
        read_session = elixirs.sessions.get_session
        record = owned(repository, elixir_id, user)
        if record.revision != body.expected_revision:
            fail(409, "revision_conflict", "This elixir changed since you opened it. Reload it and run again.", current_revision=record.revision)
        nodes = record.definition.get("nodes") or []
        catalog = catalog_lookup(node.get("preset_id") for node in nodes if node.get("kind") == "preset")
        states = read_followed(user, nodes, read_session)
        report = with_followed(validate_graph(
            record.definition,
            run_mode=record.run_mode,
            catalog=catalog,
            contracts=contracts,
            limits=limits,
            unavailable=unavailable_assets(user, nodes, resolver),
            writer=writer_for(nodes, writer_status, user),
        ), nodes, states)
        blocking = [
            issue for issue in report["issues"]
            if issue["severity"] == "error" and (issue.get("node_id") == node_id or (not issue.get("node_id") and not issue.get("edge_id")))
        ]
        if blocking:
            fail(422, "node_not_ready", "Fix this node's problems before running it.", issues=blocking, node_id=node_id)
        scoped = next((node for node in nodes if node["id"] == node_id), None)
        in_play = [node_id]
        if scoped is not None and scoped.get("kind") == "llm":
            in_play = [writer_target(nodes, record.definition.get("edges") or [], scoped)]
        pins = session_pins(nodes, in_play, states, body.session_hashes)
        try:
            run = coordinator.start_node_run(
                user,
                {"id": record.id, "revision": record.revision, "definition": record.definition, "run_mode": record.run_mode},
                contracts,
                node_id,
                body.template,
                body.idempotency_key,
                catalog=catalog,
                sessions=pins,
            )
        except BrewRefused as error:
            refused(error)
        return run_detail(coordinator, user, run)

    return router
