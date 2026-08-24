"""`backend.detect` - probe a conventionally-addressed server (default
`http://127.0.0.1:8188`, e.g. ComfyUI) and create-or-update a backend row for
it only if it's actually reachable, instead of `backend.ensure`'s "confirm
one already exists" contract (see `backend_ensure.py`'s docstring, which
explicitly punts detection to this later step).

Never imports a concrete engine's backend class: which classes exist for
`engine` comes from `BackendRegistry.get_registered_config_types()` /
`get_registered_backend_types()`, populated by that engine's own plugin via
the `backend.register` hook (see `src.features.backends.hooks`) when it's
enabled. An engine with no registered types means its plugin isn't enabled -
reported as a plain "enable the backend plugin" failure, not "server
unreachable" (those are different problems with different fixes).

Reuses the same create/update path the backend admin API uses
(`BackendConfigManager.validate_backend_config` +
`add_backend`/`update_backend` + `BackendRegistry.refresh_backends` - see
`src.features.backends.routes.BackendController.create_backend`), so a
detected backend behaves identically to one an admin created by hand.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.features.backends.backend_registry import BackendRegistry
from src.features.setup.executors._async_bridge import run_sync
from src.features.setup.executors.base import StepContext, StepResult

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8188


class BackendDetectExecutor:
    def __init__(self, backend_registry: BackendRegistry):
        self.backend_registry = backend_registry

    def execute(self, context: StepContext) -> StepResult:
        engine = context.step.params.get("engine") or context.recipe.engine
        config_types = self.backend_registry.get_registered_config_types()
        backend_types = self.backend_registry.get_registered_backend_types()
        config_cls = config_types.get(engine)
        backend_cls = backend_types.get(engine)
        if config_cls is None or backend_cls is None:
            return StepResult.fail(
                "ENGINE_PLUGIN_NOT_ENABLED",
                f"No plugin providing the '{engine}' engine is enabled, so its server can't be detected.",
                suggested_repair="Open Administration -> Plugins and enable the plugin for this engine, then retry.",
            )

        host, port = self._target(context, config_cls)
        existing = self.backend_registry.backend_config_manager.get_backends_for_engine(engine)
        base = existing[0] if existing else None

        # A new backend needs a real id up front (unlike the admin "create
        # backend" HTTP flow, which auto-generates one in the controller
        # before validation - see BackendController.create_backend) because
        # `validate_backend_config` requires `id` as already present.
        from src.platform.util.ids import generate_ulid

        probe_config = config_cls(
            **{
                **(base.model_dump() if base is not None else {}),
                "id": base.id if base is not None else generate_ulid(),
                "name": base.name if base is not None else f"{engine} (detected)",
                "engine": engine,
                "enabled": True,
                "host": host,
                "port": port,
            }
        )

        try:
            probe = self.backend_registry._create_backend_instance(probe_config)
            health = run_sync(probe.health_check())
        except Exception as exc:
            health = {"status": "error", "error": str(exc)}

        url = f"http://{host}:{port}"
        if health.get("status") != "available":
            return StepResult.fail(
                "BACKEND_UNREACHABLE",
                f"Couldn't find a {engine} server at {url}.",
                suggested_repair=(
                    f"If yours runs elsewhere, set the address in Administration -> Backends "
                    f"(detail: {health.get('error') or health.get('status')})."
                ),
            )

        data: Dict[str, Any] = probe_config.model_dump()
        validated = self.backend_registry.backend_config_manager.validate_backend_config(data)
        if base is None:
            self.backend_registry.backend_config_manager.add_backend(validated)
        else:
            self.backend_registry.backend_config_manager.update_backend(base.id, validated)
        run_sync(self.backend_registry.refresh_backends())

        return StepResult.ok(
            {
                "engine": engine,
                "backend_id": validated.id,
                "backend_name": validated.name,
                "host": host,
                "port": port,
                "created": base is None,
            }
        )

    def _target(self, context: StepContext, config_cls) -> tuple:
        params = context.step.params or {}
        base_url = params.get("base_url")
        if base_url:
            from urllib.parse import urlparse

            parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
            host = parsed.hostname or _DEFAULT_HOST
            port = parsed.port or _DEFAULT_PORT
            return host, port

        defaults = config_cls.model_fields
        host = params.get("host") or getattr(defaults.get("host"), "default", None) or _DEFAULT_HOST
        port = params.get("port") or getattr(defaults.get("port"), "default", None) or _DEFAULT_PORT
        return host, port
