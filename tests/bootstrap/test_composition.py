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
c = build_container()
r = c.chat_runtime
indexer = c.media_indexer
report = {
    "unwired": sorted(k for k, v in vars(r).items() if v is None),
    "same": {
        "preset_collaborators": r.preset_collaborators is c.preset_collaborators,
        "model_index_manager": r.model_index_manager is c.model_index_manager,
        "generation_orchestrator": r.generation_orchestrator is c.generation_orchestrator,
        "generation_history_facade": r.generation_history_facade is c.generation_history_facade,
        "generation_repository": r.generation_repository is c.generation_repository,
        "prompt_database": r.prompt_database is c.prompt_database,
        "chat_repository": r.chat_repository is c.chat_repository,
        "response_processor": r.response_processor is c.response_processor,
        "controller_runtime": c.chat_controller.chat_runtime is r,
        "controller_turns": c.chat_controller.turn_registry is c.chat_turn_registry,
        "plugin_registry": c.plugin_controller.registry is c.plugin_registry,
        "runtime_plugins": r.plugins is c.plugin_registry,
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
    },
    "shutdownable": {
        "history_executor": hasattr(c.generation_history_facade.executor, "shutdown"),
        "trace_recorder": hasattr(c.chat_call_trace_recorder, "shutdown"),
    },
    "limit_wiring": {
        "orchestrator": c.generation_orchestrator.limit_guard is c.limit_guard,
        "uploads": c.media_controller.manager.limit_guard is c.limit_guard,
        "editor": c.media_edit_controller.manager.limit_guard is c.limit_guard,
        "library": c.library_collaborators.limit_guard is c.limit_guard,
        "inspirations": c.inspiration_collaborators.limit_guard is c.limit_guard,
        "refunds": c.limit_guard.on_generation_terminal in c.generation_status_tracker._terminal_listeners,
        "plugin_kinds": c.plugin_registry.limit_kind_registry is c.limit_kind_registry,
        "core_kinds": sorted(k.key for k in c.limit_kind_registry.all() if k.source == "core"),
    },
    "failure_alerts_wired": any(
        pid == "core.admin_failure_alerts"
        for pid, _ in c.plugin_registry.hook_chain._handlers.get("generation.failed", [])
    ),
    "inference_imports": [
        m for m in ("torch", "diffusers", "transformers") if m in sys.modules
    ],
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
    assert wiring.pop("core_kinds") == ["cloud_spend_usd_month", "generations_per_day", "storage_bytes"]
    assert [k for k, v in wiring.items() if not v] == []


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
