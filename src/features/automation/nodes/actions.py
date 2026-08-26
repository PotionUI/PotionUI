"""
Built-in action node types.

Config values are Jinja-templatable against `{event, upstream}` (see plan
A6) - `_render` resolves each templatable string config field before the
action calls into its backing manager/repository/service.
"""

import asyncio
import logging
import os
from pathlib import Path
from time import monotonic
from typing import Any, Dict, List, Optional

from src.features.automation.context import NodeExecutionContext
from src.features.automation.expr import render_template
from src.features.automation.triggers.filesystem import CUSTOM_PATH_VALUE, list_app_directories, resolve_effective_directory
from src.platform.filesystem.model_types import DIRECTORY_TO_MODEL_TYPE
from src.platform.plugins.automation_nodes import NodeField, NodeResult, NodeTypeSpec, node_type_registry

logger = logging.getLogger(__name__)


def _user_options() -> List[Dict[str, str]]:
    """
    Options for the `action.assign_model` / `action.send_notification` "user"
    pickers - value=user id, label=username. `user_repo` is the module-level
    singleton (`src.features.users.repository`), same pattern
    as `tag_repo`/`automation_repo` - no DI needed, resolved fresh per
    `/api/automations/node-types` request (see `registry.resolved_config_schema`).
    """
    from src.features.users.repository import user_repo
    return [{"value": user.id, "label": user.username} for user in user_repo.get_all()]


def _backend_config_manager():
    """The configured BackendConfigManager, resolved lazily.

    Used only by the option-provider callables below (resolved per
    `/api/automations/node-types` request, like `_user_options`). The live
    container carries the manager that includes plugin-registered engines; a
    fresh manager is the fallback before composition (or in isolated tests),
    which still enumerates the always-present native backend.
    """
    try:
        from src.platform.plugins.runtime_registries import get_container
        return get_container().backend_registry.backend_config_manager
    except Exception:
        from src.features.backends.backend_config import BackendConfigManager
        return BackendConfigManager()


def _group_options() -> List[Dict[str, str]]:
    """
    Options for `action.assign_user_to_group`'s "group" picker - value=group
    id, label=group name. `user_group_repo` is the module-level singleton
    (`src.features.user_groups.repository`), same lazy-per-request pattern as
    `_user_options`/`_backend_options` above.
    """
    from src.features.user_groups.repository import user_group_repo
    return [{"value": group.id, "label": group.name} for group in user_group_repo.get_all_groups()]


def _backend_options() -> List[Dict[str, str]]:
    """Options for action.backend_action's backend picker: value=id, label=name+engine."""
    return [
        {"value": backend.id, "label": f"{backend.name} ({backend.engine})"}
        for backend in _backend_config_manager().get_backends()
    ]


def _backend_action_options() -> List[Dict[str, str]]:
    """Options for the action picker: the union of every configured backend's
    self-declared quick actions (id -> label). Core hardcodes no action ids
    here - each backend describes its own via `quick_actions()`."""
    seen: Dict[str, str] = {}
    for backend in _backend_config_manager().get_backends():
        for action in backend.quick_actions():
            seen.setdefault(action["id"], action.get("label", action["id"]))
    return [{"value": action_id, "label": label} for action_id, label in seen.items()]


def _render(value: Any, ctx: NodeExecutionContext) -> Any:
    if isinstance(value, str):
        return render_template(value, ctx.template_context())
    return value


def _guess_model_type(file_path: str, configured_type: Optional[str] = None) -> str:
    if configured_type:
        return configured_type
    parent_dir = Path(file_path).parent.name
    return DIRECTORY_TO_MODEL_TYPE.get(parent_dir, 'unknown')


async def _single_model_availability(ctx: NodeExecutionContext, model) -> tuple:
    """After a file lands in the library, learn which backends can load it.

    Availability is a fact about a backend, so the only truthful way to record
    it is to ask: each target backend is re-indexed (`index_backend`), then the
    availability table is read back for THIS model. Returns
    `(availability, notes)` - backend display name -> bool, plus one note per
    backend that could not be checked. Never raises: the file was indexed, and
    an availability hiccup must not un-succeed that.
    """
    if model is None:
        return {}, ["Availability not checked: the file was not indexed."]

    registry = ctx.services.backend_registry
    backend_indexer = ctx.services.backend_model_indexer
    if registry is None or backend_indexer is None:
        return {}, ["Availability not checked: backend indexing is not set up."]

    target_ids = _selected_backend_ids(ctx, registry)
    results, notes = await _reconcile_backend_availability(ctx, target_ids)
    if not results:
        return {}, notes

    try:
        holders = set(
            backend_indexer.availability.backend_ids_by_model([model.id]).get(model.id, [])
        )
    except Exception as exc:
        logger.warning(f"action.index_model: availability lookup for '{model.id}' failed: {exc}")
        notes.append(f"Could not read back availability: {exc}")
        return {}, notes

    availability = {
        name: (result.backend_id in holders) for name, result in results.items()
    }
    return availability, notes


async def _execute_index_model(ctx: NodeExecutionContext) -> NodeResult:
    path_template = ctx.config.get("path", "{{ event.path }}")
    file_path = _render(path_template, ctx)
    model_type = _guess_model_type(file_path, ctx.config.get("model_type"))

    indexer = ctx.services.model_indexer
    if indexer is None:
        raise RuntimeError("action.index_model: no ModelIndexer configured on AutomationServices")

    try:
        file_size = os.stat(file_path).st_size
    except OSError as exc:
        raise RuntimeError(f"action.index_model: cannot stat '{file_path}': {exc}") from exc

    model = indexer.index_single_model(file_path, model_type, file_size)

    availability, availability_notes = await _single_model_availability(ctx, model)

    # `Model` has no separate display-name field - `filename` is what the
    # rest of the app shows as the model's name (see model.py/model
    # controllers) - aliased to "name" here so downstream nodes get the
    # field they'd expect (`{{ upstream.<this_node_id>.name }}`) without
    # needing to know that `filename` is the underlying attribute.
    # `availability`/`availability_notes` are ADDITIVE - every key existing
    # graphs read downstream keeps its meaning and position.
    return NodeResult(output={
        "model_id": model.id if model else None,
        "name": model.filename if model else None,
        "filename": model.filename if model else None,
        "file_path": file_path,
        "model_type": model_type,
        "availability": availability,
        "availability_notes": availability_notes,
    })


def _skipped_backend_note(ctx: NodeExecutionContext, backend_id: str) -> str:
    """Why a selected backend id could not be indexed, by display name when known.

    A backend saved into a graph's config can later be deleted (no config row)
    or disabled (config row, but no live instance in the registry). Either way
    the run should say so and move on, not fail every other backend.
    """
    config_manager = ctx.services.backend_config_manager
    config = config_manager.get_backend(backend_id) if config_manager is not None else None
    if config is not None:
        return f"{config.name}: backend is disabled, skipped"
    return f"{backend_id}: backend no longer exists, skipped"


def _selected_backend_ids(ctx: NodeExecutionContext, registry) -> List[str]:
    """The `backends` config selection, or every backend when it's empty/absent.

    Graphs saved before the picker existed carry no `backends` key at all, so
    an empty selection must mean "all backends" to leave them unchanged.
    """
    selected = [backend_id for backend_id in (ctx.config.get("backends") or []) if backend_id]
    return selected or list(registry.get_all_backends().keys())


async def _reconcile_backend_availability(ctx: NodeExecutionContext, target_ids: List[str]):
    """Ask each target backend what it can load and reconcile the answer.

    Runs `BackendModelIndexer.index_backend` (the same call the admin
    per-backend "Index models" button makes) for every resolvable target.
    Returns `(results, notes)`: `results` maps backend display name ->
    `IndexResult` for every backend that answered; `notes` carries one line per
    backend that could not be asked (deleted, disabled, cannot list, or
    unreachable). Individual backend failures never raise.
    """
    registry = ctx.services.backend_registry
    indexer = ctx.services.backend_model_indexer

    results: Dict[str, Any] = {}
    notes: List[str] = []

    for backend_id in target_ids:
        backend = registry.get_backend(backend_id)
        if backend is None:
            notes.append(_skipped_backend_note(ctx, backend_id))
            continue
        if not backend.supports_model_listing():
            notes.append(f"{backend.name}: cannot list its models, skipped")
            continue
        try:
            results[backend.name] = await indexer.index_backend(backend)
        except Exception as exc:
            logger.warning(f"{ctx.node_type}: indexing backend '{backend_id}' failed: {exc}")
            notes.append(f"{backend.name}: indexing failed ({exc}), skipped")

    return results, notes


async def _execute_index_models(ctx: NodeExecutionContext) -> NodeResult:
    """Refresh the model index from one or more backends.

    Asks each target backend what it can load and records the answer into
    models + model_availability. An empty `backends` selection means ALL
    backends (see `_selected_backend_ids`).

    Per-backend failures (deleted, disabled, cannot list, unreachable) are
    collected as `skipped` notes and do not fail the node - unless nothing at
    all was indexed, which is a real failure and raises.
    """
    registry = ctx.services.backend_registry
    if registry is None:
        raise RuntimeError("action.index_models: no BackendRegistry configured on AutomationServices")
    if ctx.services.backend_model_indexer is None:
        raise RuntimeError("action.index_models: no BackendModelIndexer configured on AutomationServices")

    target_ids = _selected_backend_ids(ctx, registry)
    raw_results, skipped = await _reconcile_backend_availability(ctx, target_ids)

    results = {
        name: {
            "listed": result.listed,
            "created": result.created,
            "matched": result.matched,
            "removed": result.removed,
        }
        for name, result in raw_results.items()
    }

    if not results:
        detail = "; ".join(skipped) if skipped else "no backends are configured"
        raise RuntimeError(f"action.index_models: nothing was indexed - {detail}")

    return NodeResult(output={
        "results": results,
        "skipped": skipped,
        "indexed_backends": len(results),
    })


async def _execute_add_tag(ctx: NodeExecutionContext) -> NodeResult:
    model_id = _render(ctx.config.get("model_id", ""), ctx)
    tag_name = _render(ctx.config.get("tag_name", ""), ctx)
    tag_type = ctx.config.get("tag_type", "MODEL")

    tag_repo = ctx.services.tag_repository
    if tag_repo is None:
        raise RuntimeError("action.add_tag: no TagRepository configured on AutomationServices")

    tag = tag_repo.get_tag_by_name(tag_name, type=tag_type)
    if tag is None:
        tag = tag_repo.create_tag(tag_name, type=tag_type)

    added = tag_repo.add_tag_to_model(model_id, tag.id)

    return NodeResult(output={"model_id": model_id, "tag_id": tag.id, "tag_name": tag_name, "added": added})


async def _execute_assign_model(ctx: NodeExecutionContext) -> NodeResult:
    """
    Assigns a model to a user (`ModelIndexManager.assign_model_to_user` - fires
    model_index.before_assign/after_assign hooks, raises ModelAssignmentException
    on failure/block, which the engine surfaces as a failed run-node like any other).

    Assignment is idempotent here, unlike over REST: a file watcher fires again
    when the same file is touched or re-copied, and `action.index_model` dedups by
    SHA256 and hands back the SAME model id - so a second run would hit
    `UNIQUE(user_id, model_id)` and fail a workflow that had in fact done its job.
    `assigned` tells downstream nodes whether this run created the assignment.
    """
    from src.features.models.exceptions import ModelAlreadyAssignedException

    model_id = _render(ctx.config.get("model_id", ""), ctx)
    user_id = _render(ctx.config.get("user", ""), ctx)

    model_index_manager = ctx.services.model_index_manager
    if model_index_manager is None:
        raise RuntimeError("action.assign_model: no ModelIndexManager configured on AutomationServices")

    try:
        result = model_index_manager.assign_model_to_user(model_id, user_id)
        assignment = result.get("assignment")
        assigned = True
    except ModelAlreadyAssignedException as exc:
        logger.info(f"action.assign_model: {exc} - treating as already done")
        assignment = exc.assignment.to_dict() if exc.assignment is not None else None
        assigned = False

    return NodeResult(output={
        "model_id": model_id,
        "user_id": user_id,
        "assignment": assignment,
        "assigned": assigned,
    })


async def _execute_assign_user_to_group(ctx: NodeExecutionContext) -> NodeResult:
    """
    Adds a user to a user group.

    Goes straight to `UserGroupRepository.add_user_to_group` (see
    `AutomationServices.user_group_repository`) rather than through
    `src.features.user_groups.operations` - its CRUD functions all call
    `require_admin` against a live HTTP-request `User`, which an automation
    run has no equivalent of. `action.add_tag` above bypasses `src.features.tags.
    operations` for the same reason.

    Idempotent: `add_user_to_group` already no-ops (catches the
    `UNIQUE(group_id, user_id)` violation and returns None) on a duplicate
    membership, so a trigger firing twice for the same user (e.g. a retried
    `user.after_create`) never errors - `added` tells downstream nodes
    whether this run is the one that created the membership.
    """
    user_id = _render(ctx.config.get("user_id", ""), ctx)
    group_id = _render(ctx.config.get("group", ""), ctx)

    repo = ctx.services.user_group_repository
    if repo is None:
        raise RuntimeError("action.assign_user_to_group: no UserGroupRepository configured on AutomationServices")

    group = repo.get_group_by_id(group_id)
    if group is None:
        raise RuntimeError(f"action.assign_user_to_group: group '{group_id}' does not exist")

    member = repo.add_user_to_group(group_id, user_id)

    return NodeResult(output={
        "user_id": user_id,
        "group_id": group_id,
        "group_name": group.name,
        "added": member is not None,
    })


async def _execute_fetch_provider_metadata(ctx: NodeExecutionContext) -> NodeResult:
    """
    Fetch a model's metadata from a marketplace provider and store it.

    The provider is named, not hardcoded: providers are contributed by plugins and
    authenticate with their own credentials (see docs/providers.md).
    """
    model_id = _render(ctx.config.get("model_id", ""), ctx)
    provider = _render(ctx.config.get("provider", ""), ctx)

    if not provider:
        raise RuntimeError("action.fetch_provider_metadata: no provider configured")

    model_index_manager = ctx.services.model_index_manager
    if model_index_manager is None:
        raise RuntimeError(
            "action.fetch_provider_metadata: no ModelIndexManager configured on AutomationServices"
        )

    await model_index_manager.run_provider_fetch(provider, model_ids=[model_id])

    return NodeResult(output={"model_id": model_id, "provider": provider})


async def _execute_send_notification(ctx: NodeExecutionContext) -> NodeResult:
    title = _render(ctx.config.get("title", "Automation notification"), ctx)
    message = _render(ctx.config.get("message", ""), ctx)
    level = ctx.config.get("level", "info")
    user_id = ctx.config.get("user_id")

    notification_manager = ctx.services.notification_manager
    if notification_manager is None:
        raise RuntimeError("action.send_notification: no NotificationManager configured on AutomationServices")

    notifications = notification_manager.notify(
        level=level, title=title, message=message, category="automation",
        source="automation_engine", user_id=user_id,
    )

    return NodeResult(output={"title": title, "message": message, "level": level, "count": len(notifications)})


async def _execute_wait_for_gpu(ctx: NodeExecutionContext) -> NodeResult:
    """
    The wait-node: polls `GpuManager.get_free_vram()` until `threshold_mb` is
    free. No special engine "kind" is needed - it's an ordinary action that
    loops and calls `set_status("waiting")` so the run-node row (and the UI)
    reflects it while it polls. The engine's own `asyncio.wait_for` timeout
    around `execute()` bounds the wait.
    """
    import asyncio

    threshold_mb = float(ctx.config.get("threshold_mb", 2000))
    poll_interval_s = float(ctx.config.get("poll_interval_s", 5))

    gpu_manager = ctx.services.gpu_manager
    if gpu_manager is None:
        raise RuntimeError("action.wait_for_gpu: no GpuManager configured on AutomationServices")

    ctx.set_status("waiting")
    while gpu_manager.get_free_vram() < threshold_mb:
        await asyncio.sleep(max(0.5, poll_interval_s))

    return NodeResult(output={"free_vram_mb": gpu_manager.get_free_vram(), "threshold_mb": threshold_mb})


_MEDIA_INDEX_PASS_TYPES = ("tags", "clip_embed", "prompt_embed")


async def _execute_index_media_queue(ctx: NodeExecutionContext) -> NodeResult:
    """Drains the media index queue pass by pass, batch by batch.

    Bounded two ways, independently: `max_items` caps how many items a
    single fire processes, `max_runtime_s` caps how long it runs. The
    runtime deadline is only ever checked between `process_pending` calls -
    each call claims and fully settles a whole batch (every item lands
    `done` or `failed`/`pending`, see `MediaIndexRepository.claim_batch`)
    before returning - so a fire that times out always stops at a batch
    boundary. The remainder stays `pending` and the next trigger fire drains
    it via the same `claim_batch` query; nothing is left half-processed or
    dropped.
    """
    manager = ctx.services.media_index_manager
    if manager is None:
        raise RuntimeError("action.index_media_queue: no MediaIndexManager configured on AutomationServices")

    pass_types = ctx.config.get("pass_types") or list(_MEDIA_INDEX_PASS_TYPES)
    batch_size = int(ctx.config.get("batch_size", 8))
    max_items = int(ctx.config.get("max_items", 32))
    max_runtime_s = float(ctx.config.get("max_runtime_s", 60))

    processed_count = 0
    failed_count = 0
    deadline = monotonic() + max_runtime_s if max_runtime_s > 0 else None
    timed_out = False

    for pass_type in pass_types:
        while max_items <= 0 or (processed_count + failed_count) < max_items:
            if deadline is not None and monotonic() >= deadline:
                timed_out = True
                break
            result = await asyncio.to_thread(manager.process_pending, pass_type, batch_size)
            batch_total = result.get("processed", 0) + result.get("failed", 0)
            processed_count += result.get("processed", 0)
            failed_count += result.get("failed", 0)
            if batch_total < batch_size:
                break
        if timed_out:
            break

    remaining_count = 0
    for pass_type in pass_types:
        counts = manager.repository.queue_counts(pass_type).get(pass_type, {})
        remaining_count += counts.get("pending", 0)

    return NodeResult(output={
        "processed_count": processed_count,
        "remaining_count": remaining_count,
        "failed_count": failed_count,
        "timed_out": timed_out,
    })


def _resolve_scanned_model(model_repo, models_root: Optional[str], path: Path):
    """
    Look a scanned file up in the model library. `Model.file_path` is stored
    exactly as whatever string first indexed it (see
    `ModelScanner.index_single_model`) - usually absolute, since
    `action.index_model`/the file watcher hand it an absolute path - so the
    absolute form is tried first; a models-root-relative form is tried as a
    fallback for models that were indexed with a relative path.
    """
    model = model_repo.get_by_file_path(str(path))
    if model is not None or not models_root:
        return model
    try:
        rel = str(path.relative_to(models_root))
    except ValueError:
        return None
    return model_repo.get_by_file_path(rel)


async def _execute_scan_files(ctx: NodeExecutionContext) -> NodeResult:
    directory = resolve_effective_directory(ctx.config)
    if not directory:
        raise RuntimeError("action.scan_files: no directory configured")

    root = Path(directory)
    if not root.is_dir():
        raise RuntimeError(f"action.scan_files: '{directory}' does not exist or is not a directory")

    recursive = bool(ctx.config.get("recursive", True))
    extensions = {
        ext.strip().lower().lstrip(".")
        for ext in (ctx.config.get("extensions") or "").split(",")
        if ext.strip()
    }
    resolve_models = bool(ctx.config.get("resolve_models", True))
    max_files = int(ctx.config.get("max_files", 500))

    model_repo = ctx.services.model_repository if resolve_models else None
    models_root = (
        ctx.services.settings_manager.get_models_dir()
        if resolve_models and ctx.services.settings_manager is not None
        else None
    )

    glob = root.rglob("*") if recursive else root.glob("*")
    all_paths = sorted((p for p in glob if p.is_file()), key=lambda p: str(p))

    scanned = len(all_paths)
    truncated = 0 < max_files < scanned
    paths = all_paths[:max_files] if truncated else all_paths

    items: List[Dict[str, Any]] = []
    for path in paths:
        ext = path.suffix.lower().lstrip(".")
        if extensions and ext not in extensions:
            continue

        rel_parts = list(path.relative_to(root).parts)
        model = _resolve_scanned_model(model_repo, models_root, path) if model_repo is not None else None
        try:
            size = path.stat().st_size
        except OSError:
            size = None

        items.append({
            "path": str(path),
            "rel_path": "/".join(rel_parts),
            "rel_parts": rel_parts,
            "ext": path.suffix,
            "size": size,
            "model_id": model.id if model else None,
            "model_type": model.model_type if model else None,
            "model_name": model.filename if model else None,
        })

    return NodeResult(
        output={"scanned": scanned, "emitted": len(items), "truncated": truncated},
        items=items,
    )


def _collection_options() -> List[Dict[str, str]]:
    """
    Options for `action.add_to_collection`'s "collection" picker. Model
    collections are per-user (see `ModelCollectionRepository`), but this is a
    zero-arg `options_provider` resolved per `/api/automations/node-types`
    request (like `_user_options` above) with no per-request user to scope
    against - so every user's collections are enumerated and merged. Labels
    disambiguate by owner only when more than one user actually owns a
    collection; the common case (single-user install) shows plain names.
    """
    from src.features.users.repository import user_repo
    from src.features.model_library.repository.model_collection_repository import model_collection_repo

    owned = [
        (collection, user)
        for user in user_repo.get_all()
        for collection in model_collection_repo.list(user.id)
    ]
    multi_owner = len({user.id for _, user in owned}) > 1
    return [
        {
            "value": collection.id,
            "label": f"{collection.name} ({user.username})" if multi_owner else collection.name,
        }
        for collection, user in owned
    ]


async def _execute_add_to_collection(ctx: NodeExecutionContext) -> NodeResult:
    collection_id = ctx.config.get("collection", "")
    model_id = _render(ctx.config.get("model_id", ""), ctx)

    if not model_id:
        return NodeResult(output={
            "collection_id": collection_id, "model_id": model_id, "added": False, "reason": "no model_id",
        })

    repo = ctx.services.model_collection_repository
    if repo is None:
        raise RuntimeError("action.add_to_collection: no ModelCollectionRepository configured on AutomationServices")

    collection = repo.get_by_id(collection_id)
    if collection is None:
        return NodeResult(output={
            "collection_id": collection_id, "model_id": model_id, "added": False, "reason": "collection not found",
        })

    # Single-user reality: automation runs have no per-request user of their
    # own, so members are added under the collection's own owner.
    added_count = repo.add_members(collection_id, [model_id], collection.user_id)

    return NodeResult(output={
        "collection_id": collection_id, "model_id": model_id, "added": added_count > 0, "reason": None,
    })


# Action ids this node knows how to *perform*. The dropdown enumerates every
# backend's declared quick actions (see `_backend_action_options`); execution
# binds the three the native engine declares to the same call paths their admin
# routes use. An action a backend declares but that has no binding here fails
# loudly rather than silently no-op'ing.
_CLEAR_VRAM = "clear-vram"
_CLEAR_CACHE = "clear-cache"
_RESTART_BACKEND = "restart-backend"


async def _execute_backend_action(ctx: NodeExecutionContext) -> NodeResult:
    """Perform one of a backend's self-declared quick actions.

    Runs the same operation the admin quick-action endpoint runs, by calling the
    manager directly (not HTTP-to-self): `clear-vram` offloads GPU-resident
    models to host RAM (the RAM cache stays warm); `clear-cache` additionally
    invalidates the native model-lifecycle cache (drops RAM, trims the host
    allocator); `restart-backend` schedules the same in-place process restart
    as POST /api/admin/restart.

    Restart semantics: the restart is *scheduled* (a short delay), so this node
    returns normally and the run records its success before the process image is
    replaced. Any node wired after this one races the restart timer and will
    usually not run - make `restart-backend` the terminal node of a graph. The
    run that triggered the restart cannot observe events past its own success.
    """
    backend_id = ctx.config.get("backend", "")
    action_id = ctx.config.get("action", "")

    config_manager = ctx.services.backend_config_manager
    if config_manager is None:
        raise RuntimeError("action.backend_action: no BackendConfigManager configured on AutomationServices")

    backend = config_manager.get_backend(backend_id)
    if backend is None:
        raise RuntimeError(f"action.backend_action: backend '{backend_id}' not found")

    # Validate against the backend's OWN self-description, so a stale/foreign
    # action id can't be run against a backend that doesn't declare it.
    declared = {action["id"]: action for action in backend.quick_actions()}
    if action_id not in declared:
        raise RuntimeError(
            f"action.backend_action: backend '{backend_id}' declares no action '{action_id}' "
            f"(declares: {sorted(declared)})"
        )
    label = declared[action_id].get("label", action_id)

    if action_id == _CLEAR_VRAM:
        from src.platform.runtime.native.memory.residency import clear_vram

        lifecycle_manager = ctx.services.model_lifecycle_manager
        if lifecycle_manager is None:
            raise RuntimeError(
                "action.backend_action: no ModelLifecycleManager configured for the clear-vram action"
            )
        device = getattr(backend, "device", "cuda")
        result = clear_vram(device, lifecycle_manager)
        lifecycle_manager.cleanup(aggressive=False)  # gc + cuda.empty_cache(), no eviction/trim
        logger.info(
            f"action.backend_action: cleared VRAM for backend '{backend_id}' - "
            f"offloaded {result.offloaded_count} component(s), ~{result.freed_gb:.2f}GB"
            + (f" ({result.swept_count} via lifecycle-cache sweep)" if result.swept_count else "")
            + (f", {result.failed_count} failed" if result.failed_count else "")
        )
        return NodeResult(output={
            "backend_id": backend_id, "action_id": action_id, "label": label,
            "success": True, "status": "cleared",
            "offloaded_count": result.offloaded_count, "freed_gb": round(result.freed_gb, 2),
            "failed_count": result.failed_count,
        })

    if action_id == _CLEAR_CACHE:
        lifecycle_manager = ctx.services.model_lifecycle_manager
        if lifecycle_manager is None:
            raise RuntimeError(
                "action.backend_action: no ModelLifecycleManager configured for the clear-cache action"
            )
        lifecycle_manager.invalidate()  # evicts every cached model/artifact, trims RAM
        logger.info(f"action.backend_action: cleared VRAM & RAM cache for backend '{backend_id}'")
        return NodeResult(output={
            "backend_id": backend_id, "action_id": action_id, "label": label,
            "success": True, "status": "cleared",
        })

    if action_id == _RESTART_BACKEND:
        from src.features.settings.app_lifecycle import schedule_app_restart
        schedule_app_restart()
        logger.warning(f"action.backend_action: scheduled app restart for backend '{backend_id}'")
        return NodeResult(output={
            "backend_id": backend_id, "action_id": action_id, "label": label,
            "success": True, "status": "restarting",
        })

    # Declared by the backend (so it showed in the dropdown) but core has no
    # binding for it - e.g. a plugin engine's own action. Don't pretend it ran.
    raise RuntimeError(
        f"action.backend_action: action '{action_id}' on backend '{backend_id}' has no execution binding in core"
    )


def register(registry=node_type_registry) -> None:
    registry.register(NodeTypeSpec(
        key="action.index_model",
        kind="action",
        title="Index Model",
        description=(
            "Adds a model file to the library (duplicates are detected by file hash) "
            "and checks which backends can load it."
        ),
        icon="database",
        category="models",
        config_schema=[
            {"name": "path", "type": "string", "title": "File Path", "default": "{{ event.path }}",
             "templatable": True},
            # Read raw by `_guess_model_type` - never passed through `_render`, so not templatable.
            {"name": "model_type", "type": "string", "title": "Model Type (optional)", "default": ""},
            # List of backend ids (checkbox_group) - never rendered through Jinja.
            {"name": "backends", "type": "checkbox_group", "title": "Backends",
             "default": [],
             "description": "After the file is indexed, check which of these backends can load it. "
                            "Leave empty to check all backends.",
             "options_provider": _backend_options},
            {"name": "timeout_s", "type": "number", "title": "Timeout (seconds)", "default": 300},
        ],
        outputs=(
            NodeField("model_id", "string", "Model ID", "Id of the indexed model; null if indexing returned nothing."),
            NodeField("name", "string", "Name", "Display name - aliases the model's `filename`.",
                      "style.safetensors"),
            NodeField("filename", "string", "Filename", "The model's filename.", "style.safetensors"),
            NodeField("file_path", "string", "File Path", "Absolute path that was indexed."),
            NodeField("model_type", "string", "Model Type", "Configured, or guessed from the parent directory.", "lora"),
            NodeField("availability", "object", "Availability",
                      "Whether each checked backend can load this model, keyed by backend name.",
                      {"Native": True}),
            NodeField("availability_notes", "array", "Availability Notes",
                      "Notes about backends that could not be checked (deleted, disabled, or unreachable).",
                      ["Old server: backend no longer exists, skipped"]),
        ),
        execute=_execute_index_model,
    ))

    registry.register(NodeTypeSpec(
        key="action.index_models",
        kind="action",
        title="Index Models",
        description=(
            "Asks backends which models they can load and updates the model index. "
            "Pick one or more backends, or leave the list empty to index all of them."
        ),
        icon="database",
        category="models",
        config_schema=[
            # A checkbox_group's value is a LIST of backend ids - never rendered
            # through Jinja, so not templatable. Options come from the same
            # provider as action.backend_action's backend picker.
            {"name": "backends", "type": "checkbox_group", "title": "Backends",
             "default": [],
             "description": "Backends to index. Leave empty to index all backends.",
             "options_provider": _backend_options},
            {"name": "timeout_s", "type": "number", "title": "Timeout (seconds)", "default": 300},
        ],
        outputs=(
            NodeField("results", "object", "Results",
                      "Counts per indexed backend, keyed by backend name: listed, created, matched, removed.",
                      {"Native": {"listed": 42, "created": 3, "matched": 39, "removed": 1}}),
            NodeField("skipped", "array", "Skipped",
                      "One note per backend that was not indexed (deleted, disabled, or unreachable).",
                      ["Old server: backend no longer exists, skipped"]),
            NodeField("indexed_backends", "number", "Indexed Backends",
                      "How many backends were indexed.", 1),
        ),
        requires_admin=True,
        execute=_execute_index_models,
    ))

    registry.register(NodeTypeSpec(
        key="action.add_tag",
        kind="action",
        title="Add Tag",
        description="Adds a tag to a model (creates the tag if it doesn't exist).",
        icon="tag",
        category="models",
        config_schema=[
            {"name": "model_id", "type": "string", "title": "Model ID", "default": "", "templatable": True},
            {"name": "tag_name", "type": "string", "title": "Tag Name", "default": "", "templatable": True},
            {"name": "tag_type", "type": "select", "title": "Tag Type", "default": "MODEL",
             "options": [{"label": "Model", "value": "MODEL"}]},
        ],
        outputs=(
            NodeField("model_id", "string", "Model ID", "Model the tag was applied to."),
            NodeField("tag_id", "string", "Tag ID", "Id of the existing or newly created tag."),
            NodeField("tag_name", "string", "Tag Name", "The tag's name.", "krea2"),
            NodeField("added", "boolean", "Added", "False if the model already carried this tag.", True),
        ),
        execute=_execute_add_tag,
    ))

    registry.register(NodeTypeSpec(
        key="action.assign_model",
        kind="action",
        title="Assign Model to User",
        description="Assigns a model to a user's account.",
        icon="user-plus",
        category="models",
        config_schema=[
            {"name": "model_id", "type": "string", "title": "Model ID",
             "default": "", "description": "e.g. {{ upstream.<index_node_id>.model_id }}",
             "templatable": True},
            # A select, but its value *is* run through `_render` - so a template is legal here too.
            {"name": "user", "type": "select", "title": "User", "options_provider": _user_options,
             "templatable": True},
        ],
        outputs=(
            NodeField("model_id", "string", "Model ID", "Model that was assigned."),
            NodeField("user_id", "string", "User ID", "User the model was assigned to."),
            NodeField("assignment", "object", "Assignment", "The assignment record, when one was returned."),
            NodeField("assigned", "boolean", "Assigned",
                      "False when the model was already assigned to that user.", True),
        ),
        execute=_execute_assign_model,
    ))

    registry.register(NodeTypeSpec(
        key="action.assign_user_to_group",
        kind="action",
        title="Assign User to Group",
        description="Adds a user to a user group.",
        icon="users",
        category="users",
        config_schema=[
            {"name": "user_id", "type": "string", "title": "User ID",
             "default": "{{ event.user_id }}", "templatable": True},
            {"name": "group", "type": "select", "title": "Group", "options_provider": _group_options,
             "templatable": True},
        ],
        outputs=(
            NodeField("user_id", "string", "User ID", "User that was added to the group."),
            NodeField("group_id", "string", "Group ID", "Group the user was added to."),
            NodeField("group_name", "string", "Group Name", "The group's name.", "All Users"),
            NodeField("added", "boolean", "Added",
                      "False when the user was already a member of that group.", True),
        ),
        execute=_execute_assign_user_to_group,
    ))

    registry.register(NodeTypeSpec(
        key="action.fetch_provider_metadata",
        kind="action",
        title="Fetch Provider Metadata",
        description="Looks a model up on a marketplace provider and stores its metadata.",
        icon="cloud-download",
        category="models",
        config_schema=[
            {"name": "model_id", "type": "string", "title": "Model ID", "default": "", "templatable": True},
            {"name": "provider", "type": "string", "title": "Provider", "default": "", "templatable": True},
        ],
        outputs=(
            NodeField("model_id", "string", "Model ID", "Model whose metadata was fetched."),
            NodeField("provider", "string", "Provider", "Provider that was queried.", "civitai"),
        ),
        execute=_execute_fetch_provider_metadata,
    ))

    registry.register(NodeTypeSpec(
        key="action.send_notification",
        kind="action",
        title="Send Notification",
        description="Raises an in-app notification.",
        icon="bell",
        category="notify",
        config_schema=[
            {"name": "title", "type": "string", "title": "Title", "default": "", "templatable": True},
            {"name": "message", "type": "string", "title": "Message", "default": "", "templatable": True},
            {"name": "level", "type": "select", "title": "Level", "default": "info",
             "options": [
                 {"label": "Info", "value": "info"},
                 {"label": "Success", "value": "success"},
                 {"label": "Warning", "value": "warning"},
                 {"label": "Error", "value": "error"},
             ]},
            {"name": "user_id", "type": "select", "title": "User (optional, broadcasts to all if empty)",
             "allow_empty": True, "options_provider": _user_options},
        ],
        outputs=(
            NodeField("title", "string", "Title", "Rendered notification title."),
            NodeField("message", "string", "Message", "Rendered notification body."),
            NodeField("level", "string", "Level", "info | success | warning | error.", "info"),
            NodeField("count", "number", "Count", "How many notifications were raised.", 1),
        ),
        execute=_execute_send_notification,
    ))

    registry.register(NodeTypeSpec(
        key="action.wait_for_gpu",
        kind="action",
        title="Wait For GPU",
        description="Waits (polling) until free VRAM reaches a threshold, or the node timeout elapses.",
        icon="hourglass",
        category="gpu",
        config_schema=[
            {"name": "threshold_mb", "type": "number", "title": "Threshold (MB free)", "default": 2000},
            {"name": "poll_interval_s", "type": "number", "title": "Poll Interval (seconds)", "default": 5},
            {"name": "timeout_s", "type": "number", "title": "Timeout (seconds)", "default": 300},
        ],
        outputs=(
            NodeField("free_vram_mb", "number", "Free VRAM (MB)", "Free VRAM once the wait cleared.", 4096),
            NodeField("threshold_mb", "number", "Threshold (MB)", "The threshold that was waited for.", 2000),
        ),
        execute=_execute_wait_for_gpu,
    ))

    registry.register(NodeTypeSpec(
        key="action.index_media_queue",
        kind="action",
        title="Index Media Queue",
        description=(
            "Drains the gallery indexing queue: system tags, smart-search (CLIP) embeddings, "
            "and prompt-search (text) embeddings."
        ),
        icon="images",
        category="media",
        config_schema=[
            {"name": "pass_types", "type": "checkbox_group", "title": "Passes",
             "default": list(_MEDIA_INDEX_PASS_TYPES),
             "options": [
                 {"label": "Tags", "value": "tags"},
                 {"label": "Smart Search (image embeddings)", "value": "clip_embed"},
                 {"label": "Prompt Search (text embeddings)", "value": "prompt_embed"},
             ]},
            {"name": "batch_size", "type": "number", "title": "Batch Size", "default": 8},
            {"name": "max_items", "type": "number", "title": "Max Items (0 = drain until empty)", "default": 32},
            {"name": "max_runtime_s", "type": "number", "title": "Max Runtime (seconds, 0 = no limit)",
             "default": 60,
             "description": "Yields the worker after this many seconds even if items remain; "
                            "the remainder stays queued for the next trigger fire."},
        ],
        outputs=(
            NodeField("processed_count", "number", "Processed", "Items successfully indexed.", 24),
            NodeField("remaining_count", "number", "Remaining", "Items still queued across the selected passes.", 6),
            NodeField("failed_count", "number", "Failed", "Items that failed indexing.", 0),
            NodeField("timed_out", "boolean", "Timed Out",
                      "Whether max_runtime_s was hit before the queue drained.", False),
        ),
        execute=_execute_index_media_queue,
    ))

    registry.register(NodeTypeSpec(
        key="action.backend_action",
        kind="action",
        title="Backend Action",
        description=(
            "Runs one of a backend's admin quick actions (e.g. Clear VRAM, Restart Backend). "
            "Admin-only. Restart is scheduled and interrupts the run - make it the terminal node."
        ),
        icon="server",
        category="backends",
        config_schema=[
            {"name": "backend", "type": "select", "title": "Backend", "options_provider": _backend_options},
            {"name": "action", "type": "select", "title": "Action", "options_provider": _backend_action_options},
        ],
        outputs=(
            NodeField("backend_id", "string", "Backend ID", "Backend the action ran against.", "native"),
            NodeField("action_id", "string", "Action ID", "The quick action that was performed.", "clear-vram"),
            NodeField("label", "string", "Label", "Human-readable action label.", "Clear VRAM"),
            NodeField("success", "boolean", "Success", "True when the action was performed.", True),
            NodeField("status", "string", "Status", "Short outcome: e.g. 'cleared' or 'restarting'.", "cleared"),
            NodeField("offloaded_count", "number", "Offloaded",
                      "clear-vram only: GPU-resident components actually offloaded (0 if nothing was resident).", 0),
            NodeField("freed_gb", "number", "Freed (GB)", "clear-vram only: estimated VRAM freed.", 0.0),
            NodeField("failed_count", "number", "Failed", "clear-vram only: components that failed to offload.", 0),
        ),
        requires_admin=True,
        execute=_execute_backend_action,
    ))

    registry.register(NodeTypeSpec(
        key="action.scan_files",
        kind="action",
        title="Scan Files",
        description="Walks a directory and fans out one item per file, optionally resolved against the model library.",
        icon="folder-search",
        category="models",
        config_schema=[
            {"name": "directory", "type": "select", "title": "Directory", "options_provider": list_app_directories},
            {"name": "custom_path", "type": "textbox", "title": "Custom Path", "visible": False,
             "reactions": [
                 {"when": {"field": "directory", "equals": CUSTOM_PATH_VALUE}, "then": {"set_visibility": True}},
                 {"when": {"field": "directory", "not_equals": CUSTOM_PATH_VALUE}, "then": {"set_visibility": False}},
             ]},
            {"name": "recursive", "type": "checkbox", "title": "Include subdirectories", "default": True},
            {"name": "extensions", "type": "string", "title": "Extensions (comma-separated, optional)", "default": "",
             "description": "e.g. \"safetensors, ckpt\" - leave empty to include every file."},
            {"name": "resolve_models", "type": "checkbox", "title": "Resolve to indexed models", "default": True,
             "description": "Look each file up in the model library by file path and fill "
                            "model_id/model_type/model_name."},
            {"name": "max_files", "type": "number", "title": "Max Files (0 = no limit)", "default": 500},
        ],
        outputs=(
            NodeField("scanned", "number", "Scanned", "Files found before the Max Files cap.", 128),
            NodeField("emitted", "number", "Emitted", "Items emitted downstream (after the cap and extension filter).", 128),
            NodeField("truncated", "boolean", "Truncated", "True when more files were found than Max Files.", False),
        ),
        item_outputs=(
            NodeField("path", "string", "Path", "Absolute path of the file.",
                      "/home/u/models/loras/krea2/style.safetensors"),
            NodeField("rel_path", "string", "Relative Path", "Path relative to the scanned directory.",
                      "krea2/style.safetensors"),
            NodeField("rel_parts", "array", "Relative Parts",
                      "Path components relative to the scanned directory.", ["krea2", "style.safetensors"]),
            NodeField("ext", "string", "Extension", "File suffix, including the dot.", ".safetensors"),
            NodeField("size", "number", "Size", "File size in bytes.", 1234567),
            NodeField("model_id", "string", "Model ID", "Resolved model id, or null when unresolved.", "01H..."),
            NodeField("model_type", "string", "Model Type", "Resolved model type, or null.", "lora"),
            NodeField("model_name", "string", "Model Name", "Resolved model filename, or null.", "style.safetensors"),
        ),
        execute=_execute_scan_files,
    ))

    registry.register(NodeTypeSpec(
        key="action.add_to_collection",
        kind="action",
        title="Add to Collection",
        description="Adds a model to a model collection.",
        icon="folder-plus",
        category="models",
        config_schema=[
            {"name": "collection", "type": "select", "title": "Collection", "options_provider": _collection_options},
            {"name": "model_id", "type": "string", "title": "Model ID", "default": "",
             "description": "e.g. {{ upstream.<scan_node_id>.model_id }}", "templatable": True},
        ],
        outputs=(
            NodeField("collection_id", "string", "Collection ID", "Collection targeted by this run.", "01H..."),
            NodeField("model_id", "string", "Model ID", "Rendered model id; empty when skipped.", "01H..."),
            NodeField("added", "boolean", "Added",
                      "False when nothing was added (missing model_id, unknown collection, or already a member).", True),
            NodeField("reason", "string", "Reason", "Why nothing was added; null on success.", "no model_id"),
        ),
        execute=_execute_add_to_collection,
    ))
