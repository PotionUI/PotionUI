"""Guards for what `build_container()` composes.

The container used to construct `ChatRuntime` early with `None` placeholders and
complete it with seventeen attribute assignments 500 lines later. Anything that
read a collaborator off the runtime between those two points saw `None`, and
nothing in the type system said so. `build_chat` now takes every collaborator up
front; these tests pin that, the instances that must be shared rather than
rebuilt, and that composing the process pulls in no inference stack.

Composing a container is done in a subprocess. `Database` is a singleton that
fixes its path from `POTIONUI_DB_PATH` the first time it is constructed, and
importing anything under `src.bootstrap` constructs it - so by the time a
fixture could set the variable, the handle is already bound to the real
`storage/db.sqlite`. A child process with the variable set in its environment is
the only way to compose against a scratch database.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.bootstrap.composition import ChatDeps, build_chat

ROOT = Path(__file__).resolve().parents[2]

_PROBE = r"""
import json, sys
from src.bootstrap.app import run_migrations_sync
from src.bootstrap.container import build_container
from src.features.generation.routes import _get_generation_controller as _generation_controller

run_migrations_sync()
from src.platform.websocket.notification_connection_hub import notification_connection_hub
c = build_container()
r = c.chat_runtime
indexer = c.media_indexer
report = {
    "unwired": sorted(k for k, v in {**vars(r), **vars(r.tool_services)}.items() if v is None),
    "same": {
        "preset_collaborators": r.tool_services.preset_collaborators is c.preset_collaborators,
        "model_index_manager": r.tool_services.model_index_manager is c.model_index_manager,
        "generation_orchestrator": r.tool_services.generation_orchestrator is c.generation_orchestrator,
        "generation_history_facade": r.tool_services.generation_history_facade is c.generation_history_facade,
        "generation_repository": r.generation_repository is c.generation_repository,
        "prompt_database": r.tool_services.prompt_database is c.prompt_database,
        "chat_repository": r.chat_repository is c.chat_repository,
        "response_processor": r.response_processor is c.response_processor,
        "controller_runtime": c.chat_controller.chat_runtime is r,
        "controller_turns": c.chat_controller.turn_registry is c.chat_turn_registry,
        "plugin_registry": c.plugin_controller.registry is c.plugin_registry,
        "runtime_plugins": r.plugins is c.plugin_registry,
        "prompt_writer_plugins": c.prompt_enhancement_manager.plugins is c.plugin_registry,
        "indexer_plugins": indexer.plugins is c.plugin_registry,
        "llm_provider_registry": c.llm_service.types.provider_registry is c.plugin_registry.llm_provider_registry,
        "llm_profile_resolver": c.llm_controller.profile_resolver is c.llm_service.profiles,
        "facade_repo": c.generation_history_facade.generation_repo is c.generation_repository,
        "parameter_repository": (
            c.inspiration_collaborators.generation_parameter_repository
            is r.generation_parameter_repository
        ),
        "chroma_provider": (
            indexer.gallery_vector_store._client_provider
            is indexer.gallery_prompt_vector_store._client_provider
        ),
        "router_model_index": (
            c.generation_orchestrator.router is not None
            and c.generation_orchestrator.router._model_index is c.model_index_manager
        ),
    },
    "cloud_wiring": {
        "orchestrator_policy_has_scopes": c.generation_orchestrator.cloud_policy.scope_repository is not None,
        "policy_scopes_are_the_admin_scopes": (
            c.generation_orchestrator.cloud_policy.scope_repository is c.cloud_model_scopes.repository
        ),
        "model_catalog_labels_cloud_models_from_the_container_registry": (
            c.model_index_manager.catalog.cloud_presentation is not None
            and c.model_index_manager.catalog.cloud_presentation.backend_registry is c.backend_registry
        ),
        "orchestrator_capabilities_are_the_container_capabilities": (
            c.generation_orchestrator.cloud_capabilities is c.cloud_capabilities
        ),
    },
    "generation_output": {
        "broadcaster_uses_the_container_hub": c.output_broadcaster.connection_hub is c.connection_hub,
        "orchestrator_uses_the_broadcaster": c.generation_orchestrator.output_broadcaster is c.output_broadcaster,
        "controller_uses_the_broadcaster": _generation_controller(c).output_broadcaster is c.output_broadcaster,
        "websocket_handler_uses_the_container_hub": _generation_controller(c).websocket_handler.connection_hub is c.connection_hub,
        "model_files_are_indexed_by_the_container_coordinator": (
            c.output_processor.model_file_indexer == c.model_index_manager.indexing.index_downloaded
        ),
    },
    "shutdownable": {
        "history_executor": hasattr(c.generation_history_facade.executor, "shutdown"),
        "trace_recorder": hasattr(c.chat_call_trace_recorder, "shutdown"),
    },
    "limit_wiring": {
        "orchestrator": c.generation_orchestrator.limit_guard is c.limit_guard,
        "uploads": c.media_controller.manager.limit_guard is c.limit_guard,
        "media_tools": c.media_tool_service.publisher.limit_guard is c.limit_guard,
        "library": c.library_collaborators.limit_guard is c.limit_guard,
        "inspirations": c.inspiration_collaborators.limit_guard is c.limit_guard,
        "refunds": c.limit_guard.on_generation_terminal in c.generation_status_tracker._terminal_listeners,
        "plugin_kinds": c.plugin_registry.limit_kind_registry is c.limit_kind_registry,
        "core_kinds": sorted(k.key for k in c.limit_kind_registry.all() if k.source == "core"),
    },
    "tab_draft_wiring": {
        "controller_uses_the_container_manager": c.tab_draft_controller.manager is c.tab_draft_manager,
        "manager_has_a_repository": c.tab_draft_manager.repository is not None,
        "chat_tools_use_the_container_manager": c.chat_runtime.tool_services.tab_drafts is c.tab_draft_manager,
        "mcp_tools_use_the_container_manager": c.mcp_tool_collaborators.services.tab_drafts is c.tab_draft_manager,
        "pushes_go_to_the_notification_hub": c.tab_draft_manager.notifier is notification_connection_hub,
        "housekeeping_sweeps_the_container_manager": any(
            t.name == "tab_drafts" for t in c.housekeeping_worker.tasks
        ),
    },
    "media_tool_wiring": {
        "chat_tools_use_the_container_service": c.chat_runtime.tool_services.media_tool_service is c.media_tool_service,
        "mcp_tools_use_the_container_service": c.mcp_tool_collaborators.services.media_tool_service is c.media_tool_service,
        "automation_nodes_use_the_container_service": (
            c.automation_controller.manager.engine.services.media_tool_service is c.media_tool_service
        ),
        "the_chat_tool_reads_the_container_catalog": c.tool_registry.get("run_media_tool")._registry is c.media_tool_registry,
        "automation_services_hold_the_container_catalog": (
            c.automation_controller.manager.engine.services.media_tool_registry is c.media_tool_registry
        ),
        "the_automation_picker_lists_plugin_catalog_tools": any(
            o["value"] == "trim" for o in __import__("src.features.automation.nodes.actions", fromlist=["x"])._media_tool_options()
        ),
        "the_service_reads_the_container_catalog": c.media_tool_service.registry is c.media_tool_registry,
        "generation_engines_inject_the_container_catalog_into_pipes": (
            c.backend_registry.generation_engine_factory().media_tools is c.media_tool_registry
        ),
    },
    "turn_store_wiring": {
        "engine_uses_the_container_store": c.turn_engine._store is c.turn_store,
        "store_is_the_durable_one": type(c.turn_store).__name__ == "SqliteTurnStore",
    },
    "detected_tags_wired": any(
        pid == "organize_engine"
        for pid, _ in c.plugin_registry.hook_chain._handlers.get("media_index.after_tags_written", [])
    ),
    "failure_alerts_wired": any(
        pid == "core.admin_failure_alerts"
        for pid, _ in c.plugin_registry.hook_chain._handlers.get("generation.failed", [])
    ),
    "inference_imports": [
        m for m in ("torch", "diffusers", "transformers") if m in sys.modules
    ],
}
from types import SimpleNamespace
from src.features.generation.status_tracker import GenerationState
governor = c.model_index_manager.indexing.scanner.governor
c.backend_registry.get_backends_for_engine = lambda engine: [
    SimpleNamespace(backend_id="local-1", config=SimpleNamespace(driver="native.local"))
]
idle = governor.throttle.generation_running()
c.generation_status_tracker.create("g-cloud", backend_id="cloud-1").state = GenerationState.RUNNING
other_backend = governor.throttle.generation_running()
c.generation_status_tracker.create("g-local", backend_id="local-1").state = GenerationState.RUNNING
report["indexing_wiring"] = {
    "indexing_starts_at_the_low_level": governor.level() == "low",
    "an_idle_app_does_not_pause_indexing": idle is False,
    "a_generation_on_another_backend_does_not_pause_indexing": other_backend is False,
    "a_running_local_generation_pauses_indexing": governor.throttle.generation_running() is True,
}
digests = c.model_index_manager.digests
indexing = c.model_index_manager.indexing
scanner = indexing.scanner
report["digest_wiring"] = {
    "the_scanner_hands_large_files_to_the_digest_pool": scanner.digests is digests,
    "the_pool_writes_under_the_scanners_lock": digests._write_lock is scanner.write_lock,
    "the_pool_follows_the_same_level_and_pause_rules": digests.governor is governor,
    "the_pool_reports_applied_digests_to_the_coordinator": digests._on_applied == indexing.on_digests_applied,
    "the_scanner_reports_contested_files_to_the_coordinator": scanner.contested_listener == indexing._on_contested_resolved,
    "the_pool_resolves_paths_through_the_scanner": digests._path_for == scanner.location_path,
    "the_pool_is_not_running_until_the_lifespan_starts_it": digests._running is False,
    "provider_fetch_hashes_through_the_pool": c.model_index_manager.provider_info.digests is digests,
    "setting_a_type_can_ask_the_pool_for_a_digest": c.model_index_manager.types._digests is digests,
    "remote_backends_hash_bundled_models_through_the_pool": c.backend_registry.model_digests is digests,
    "recipe_listings_look_for_renamed_files_through_the_pool": c.recipe_slot_variants._digests is digests,
    "download_verification_is_paced_by_the_same_governor": c.download_queue.verify_throttle == governor.verification_throttle,
    "preset_requirements_can_count_unverified_files": c.model_index_manager.digests.pending_count() == 0,
    "the_recipe_plan_looks_for_renamed_files_through_the_pool": c.recipe_runner._executor_registry.get_executor("artifacts.plan")._digests is digests,
    "the_recipe_fetch_looks_for_renamed_files_through_the_pool": c.recipe_runner._executor_registry.get_executor("artifacts.fetch")._digests is digests,
}
from fastapi import FastAPI
from src.bootstrap.routers import register_routers
elixir_app = FastAPI()
register_routers(elixir_app, c)
report["elixir_routes"] = sorted(
    f"{method} {route.path}"
    for route in elixir_app.routes
    if getattr(route, "path", "").startswith("/api/elixirs")
    for method in route.methods
)
elixir_port = c.elixirs.coordinator.port
elixir_hooks = (
    "generation.after_complete", "generation.before_delete", "generation.before_bulk_delete",
    "session.after_update", "session.after_delete",
)
report["elixirs_wiring"] = {
    "every_hook_reaches_the_runtime": all(
        any(pid == "elixirs_engine" for pid, _ in c.plugin_registry.hook_chain._handlers.get(name, []))
        for name in elixir_hooks
    ),
    "the_runtime_drives_the_routes_coordinator": c.elixirs.runtime.coordinator is c.elixirs.coordinator,
    "the_runtime_is_not_running_until_the_lifespan_starts_it": c.elixirs.runtime.running is False,
    "brews_submit_through_the_container_orchestrator": elixir_port.orchestrator is c.generation_orchestrator,
    "brews_read_history_through_the_container_facade": elixir_port.history is c.generation_history_facade,
    "brews_resolve_library_files_through_the_container_media_store": elixir_port.media_store is c.media_store,
    "prompts_are_written_by_the_container_prompt_writer": elixir_port.prompt_writer is c.prompt_enhancement_manager,
    "owners_are_looked_up_in_the_container_users": elixir_port.users is c.user_repository,
    "sessions_are_read_from_the_container_repositories": (
        c.elixirs.sessions.sessions is c.session_repository
        and c.elixirs.sessions.versions is c.session_version_repository
    ),
    "follows_read_sessions_through_the_same_reader": c.elixirs.runtime.follows.reader == c.elixirs.sessions.get_session,
    "the_limits_come_from_the_seeded_settings": c.elixirs.limits().max_nodes == 24,
}
import src.platform.plugins.runtime_registries as runtime_registries
report["recipe_smoke_wiring"] = {
    "the_smoke_asks_the_registry_for_the_app_loop": c.recipe_runner._executor_registry.get_executor("generation.smoke").app_loop is runtime_registries.get_app_loop,
}
print("COMPOSITION_REPORT=" + json.dumps(report))
"""


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    """Compose a real container in a child process over a scratch database."""
    workdir = tmp_path_factory.mktemp("composition")
    env = {
        "POTIONUI_DB_PATH": str(workdir / "db.sqlite"),
        "PYTHONPATH": f"{ROOT / 'venv/lib/python3.12/site-packages'}{os.pathsep}{ROOT}",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(workdir),
    }
    for name in ("SYSTEMROOT", "TEMP", "TMP", "PATHEXT", "COMSPEC"):
        if name in os.environ:
            env[name] = os.environ[name]
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith("COMPOSITION_REPORT=")]
    assert len(lines) == 1, result.stdout[-4000:]
    return json.loads(lines[0].split("=", 1)[1])


def test_chat_runtime_has_no_unwired_collaborator(report):
    """Every collaborator the tool context reaches is set at construction."""
    assert report["unwired"] == []


def test_chat_components_share_the_container_instances(report):
    shared = report["same"]
    assert [k for k, v in shared.items() if not v] == []


def test_generation_outputs_go_through_one_broadcaster_and_one_hub(report):
    assert [k for k, v in report["generation_output"].items() if not v] == []


def test_the_cloud_policy_enforces_the_same_scopes_the_admin_edits(report):
    assert [k for k, v in report["cloud_wiring"].items() if not v] == []


def test_shutdown_dependent_components_are_present(report):
    """The lifespan's shutdown sequence has something to drain."""
    assert [k for k, v in report["shutdownable"].items() if not v] == []


def test_every_submit_and_upload_path_shares_the_one_limit_guard(report):
    wiring = dict(report["limit_wiring"])
    assert wiring.pop("core_kinds") == ["cloud_spend_usd_month", "generations_per_day", "storage_bytes", "upload_file_size"]
    assert [k for k, v in wiring.items() if not v] == []


def test_indexing_pauses_only_for_a_generation_running_on_this_machine(report):
    assert [k for k, v in report["indexing_wiring"].items() if not v] == []


def test_the_digest_pool_is_wired_to_the_scanner_and_the_coordinator(report):
    assert [k for k, v in report["digest_wiring"].items() if not v] == []


def test_the_recipe_smoke_reaches_the_app_loop_through_the_raising_registry_lookup(report):
    assert [k for k, v in report["recipe_smoke_wiring"].items() if not v] == []


def test_tab_drafts_are_composed_and_swept_by_housekeeping(report):
    assert [k for k, v in report["tab_draft_wiring"].items() if not v] == []


def test_chat_mcp_and_automation_run_media_tools_through_the_one_service_and_catalog(report):
    assert [k for k, v in report["media_tool_wiring"].items() if not v] == []


def test_chat_turns_are_stored_durably_by_the_one_container_store(report):
    assert [k for k, v in report["turn_store_wiring"].items() if not v] == []

def test_organize_listens_to_the_tags_the_media_indexer_announces(report):
    assert report["detected_tags_wired"] is True


def test_elixirs_are_composed_from_the_container_and_listen_to_their_hooks(report):
    assert [k for k, v in report["elixirs_wiring"].items() if not v] == []


def test_the_elixirs_api_is_mounted_on_the_app(report):
    routes = report["elixir_routes"]
    assert {
        "GET /api/elixirs",
        "POST /api/elixirs",
        "GET /api/elixirs/presets",
        "GET /api/elixirs/{elixir_id}",
        "PUT /api/elixirs/{elixir_id}",
        "DELETE /api/elixirs/{elixir_id}",
        "POST /api/elixirs/{elixir_id}/validate",
        "GET /api/elixirs/{elixir_id}/followed-sessions",
        "POST /api/elixirs/{elixir_id}/nodes/{node_id}/session",
        "POST /api/elixirs/{elixir_id}/nodes/{node_id}/session/follow",
        "POST /api/elixirs/{elixir_id}/nodes/{node_id}/session/copy",
        "POST /api/elixirs/{elixir_id}/nodes/{node_id}/session/new",
        "POST /api/elixirs/{elixir_id}/runs",
        "GET /api/elixirs/{elixir_id}/runs",
        "POST /api/elixirs/{elixir_id}/nodes/{node_id}/runs",
        "GET /api/elixirs/runs/{run_id}",
        "POST /api/elixirs/runs/{run_id}/cancel",
        "POST /api/elixirs/runs/{run_id}/edges/{edge_id}/continue",
        "PUT /api/elixirs/runs/{run_id}/edges/{edge_id}/selection",
        "PUT /api/elixirs/runs/{run_id}/texts/{text_id}",
        "POST /api/elixirs/runs/{run_id}/nodes/{node_id}/retry-failed",
    } <= set(routes)


def test_admin_failure_alerts_listen_to_generation_failed(report):
    assert report["failure_alerts_wired"] is True


def test_building_the_container_imports_no_inference_stack(report):
    """Composition must stay importable on a box with no torch and no GPU."""
    assert report["inference_imports"] == []


def test_llm_repository_is_not_rebuilt_at_import():
    """Its TTL cache and default-provider snapshot are per-instance.

    A module-level `LLMRepository()` gave the process a second cache nobody
    invalidated, and read a setting from the database at import time.
    """
    import src.features.llm.repository as repository_module

    instances = [
        name
        for name, value in vars(repository_module).items()
        if isinstance(value, repository_module.LLMRepository)
    ]
    assert instances == []


class _StubSettings:
    def get_setting(self, key, default=None):
        return default


def _stub_chat_deps():
    values = {field.name: object() for field in dataclasses.fields(ChatDeps)}
    values["settings"] = _StubSettings()
    return ChatDeps(**values)


def test_build_chat_passes_every_dependency_to_the_runtime():
    """No `ChatDeps` field may be accepted and then dropped on the floor."""
    deps = _stub_chat_deps()
    components = build_chat(deps)

    on_runtime = set(map(id, vars(components.chat_runtime).values()))
    dropped = sorted(
        field.name
        for field in dataclasses.fields(ChatDeps)
        if field.name not in ("settings", "llm_repository")
        and id(getattr(deps, field.name)) not in on_runtime
    )
    assert dropped == []
    assert components.chat_runtime.pre_chat_action_registry is components.pre_chat_action_registry
    assert components.chat_controller.chat_runtime is components.chat_runtime
