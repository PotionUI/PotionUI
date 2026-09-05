"""`src.features.presets.operations.get_preset_requirements` and the
`requirements_summary` peek wired into `list_presets`/`get_preset`.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.features.backends.in_process_backend import InProcessBackend
from src.features.presets import operations
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.exceptions import PresetNotFoundException
from src.features.presets.requirements.builtin import register_builtin_requirement_checkers
from src.features.presets.requirements.contracts import RequirementResult
from src.features.presets.requirements.evaluator import RequirementsCache
from src.features.presets.templates import PresetTemplate
from src.platform.plugins.requirement_checkers import (
    RequirementCheckerRegistration,
    requirement_checker_registry,
)


def setup_module(module):
    # This module's assertions rely on the shared `requirement_checker_registry`
    # singleton carrying the core checkers - lazily seed it if some earlier
    # test module hasn't already (mirrors `PresetLinter._requirement_checker_registry`'s
    # guard), so this file's results don't depend on collection order.
    if not requirement_checker_registry.all():
        register_builtin_requirement_checkers(requirement_checker_registry)


def _preset(preset_id="preset-1", requirements=None):
    return PresetTemplate(
        id=preset_id, name="Preset", version="1.0.0", path="/tmp/preset", modes={},
        engine="native", requirements=requirements or [],
    )


def _collaborators(**overrides):
    defaults = dict(
        preset_loader=MagicMock(),
        preset_processor=MagicMock(),
        template_processor=MagicMock(),
        file_repo=MagicMock(),
        db_repo=MagicMock(),
        user_repo=MagicMock(),
        group_repo=MagicMock(),
        pipeline_builder=MagicMock(),
        pipe_catalog=MagicMock(),
        plugins=MagicMock(),
        settings=MagicMock(),
    )
    defaults.update(overrides)
    with patch('src.features.presets.collaborators.PresetFormSerializer'):
        return PresetCollaborators(**defaults)


class TestGetPresetRequirements:
    @pytest.mark.asyncio
    async def test_raises_when_preset_not_found(self):
        collaborators = _collaborators(file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=None)))

        with pytest.raises(PresetNotFoundException):
            await operations.get_preset_requirements(collaborators, "missing-preset")

    @pytest.mark.asyncio
    async def test_returns_results_and_summary(self):
        preset = _preset(requirements=[{"type": "platform", "os": ["linux"]}])
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            backend_registry=None,
            model_index=None,
            gpu_monitor=None,
            requirements_cache=RequirementsCache(),
        )

        data = await operations.get_preset_requirements(collaborators, preset.id)

        assert data["summary"]["ok"] == 1
        assert len(data["results"]) == 1
        assert data["results"][0]["status"] == "ok"
        assert "checked_at" in data

    @pytest.mark.asyncio
    async def test_optional_miss_counts_as_optional_missing_not_missing(self):
        preset = _preset(requirements=[
            {"type": "platform", "os": ["linux"]},  # ok on this host
            {"type": "python_package", "name": "definitely-not-a-real-package-xyz", "optional": True},
            {"type": "python_package", "name": "also-not-a-real-package-xyz"},  # non-optional miss
        ])
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            requirements_cache=RequirementsCache(),
        )

        data = await operations.get_preset_requirements(collaborators, preset.id)

        assert data["summary"] == {"ok": 1, "missing": 1, "unknown": 0, "optional_missing": 1}
        optional_item = data["results"][1]
        assert optional_item["optional"] is True
        assert optional_item["status"] == "missing"

    @pytest.mark.asyncio
    async def test_result_carries_type_name_and_optional(self):
        preset = _preset(requirements=[
            {"type": "platform", "os": ["linux"], "optional": True},
        ])
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            requirements_cache=RequirementsCache(),
        )

        data = await operations.get_preset_requirements(collaborators, preset.id)

        item = data["results"][0]
        assert item["type"] == "platform"
        assert item["name"] == "linux"  # PlatformRequirementChecker.describe()
        assert item["optional"] is True

    @pytest.mark.asyncio
    async def test_optional_defaults_to_false(self):
        preset = _preset(requirements=[{"type": "platform", "os": ["linux"]}])
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            requirements_cache=RequirementsCache(),
        )

        data = await operations.get_preset_requirements(collaborators, preset.id)

        assert data["results"][0]["optional"] is False

    @pytest.mark.asyncio
    async def test_unregistered_type_falls_back_to_first_string_field(self):
        preset = _preset(requirements=[{"type": "custom_check", "node": "MyCustomNode"}])
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            requirements_cache=RequirementsCache(),
        )

        data = await operations.get_preset_requirements(collaborators, preset.id)

        item = data["results"][0]
        assert item["type"] == "custom_check"
        assert item["name"] == "MyCustomNode"
        assert item["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_unregistered_type_with_no_string_field_falls_back_to_type(self):
        preset = _preset(requirements=[{"type": "custom_check", "count": 3}])
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            requirements_cache=RequirementsCache(),
        )

        data = await operations.get_preset_requirements(collaborators, preset.id)

        assert data["results"][0]["name"] == "custom_check"

    @pytest.mark.asyncio
    async def test_no_cache_still_evaluates(self):
        preset = _preset(requirements=[{"type": "platform", "os": ["linux"]}])
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            requirements_cache=None,
        )

        data = await operations.get_preset_requirements(collaborators, preset.id)

        assert data["summary"]["ok"] == 1

    @pytest.mark.asyncio
    async def test_refresh_flag_forces_reevaluation(self):
        preset = _preset(requirements=[{"type": "platform", "os": ["linux"]}])
        cache = RequirementsCache()
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            requirements_cache=cache,
        )

        first = await operations.get_preset_requirements(collaborators, preset.id)
        second = await operations.get_preset_requirements(collaborators, preset.id, refresh=True)

        assert first["checked_at"] <= second["checked_at"]


class TestRequirementsSummaryPeek:
    def test_get_preset_summary_is_none_before_evaluation(self):
        preset = _preset()
        collaborators = _collaborators(
            file_repo=MagicMock(
                find_preset_by_id=MagicMock(return_value=preset),
                preset_to_info=MagicMock(return_value=MagicMock(dict=lambda: {
                    "id": preset.id, "name": preset.name, "version": preset.version,
                })),
            ),
            requirements_cache=RequirementsCache(),
        )

        data = operations.get_preset(collaborators, preset.id)

        assert data["requirements_summary"] is None

    @pytest.mark.asyncio
    async def test_get_preset_summary_populated_after_evaluation(self):
        preset = _preset(requirements=[{"type": "platform", "os": ["linux"]}])
        cache = RequirementsCache()
        collaborators = _collaborators(
            file_repo=MagicMock(
                find_preset_by_id=MagicMock(return_value=preset),
                preset_to_info=MagicMock(return_value=MagicMock(dict=lambda: {
                    "id": preset.id, "name": preset.name, "version": preset.version,
                })),
            ),
            requirements_cache=cache,
        )

        await operations.get_preset_requirements(collaborators, preset.id)
        data = operations.get_preset(collaborators, preset.id)

        assert data["requirements_summary"] == {"ok": 1, "missing": 0, "unknown": 0, "optional_missing": 0}


class _FixtureBackendChecker:
    """A "backend"-scoped fixture checker: "ok" for `ok_backend_id`,
    "missing" for any other backend."""

    type = "fixture_backend_check"
    schema = None
    scope = "backend"

    def __init__(self, ok_backend_id: str):
        self.ok_backend_id = ok_backend_id

    async def check(self, spec, ctx):
        backend_id = ctx.backend.id if ctx.backend else None
        if backend_id == self.ok_backend_id:
            return RequirementResult(status="ok", detail=f"present on {backend_id}")
        return RequirementResult(status="missing", detail=f"absent on {backend_id}")


class _FakeBackendConfig:
    def __init__(self, id, name, engine, driver=None):
        self.id = id
        self.name = name
        self.engine = engine
        self.driver = driver or engine


class _FakeBackend:
    def __init__(self, config, execution_device="unestablished"):
        self.config = config
        self.backend_id = config.id
        self.name = config.name
        self.engine = config.engine
        # Mirrors the real `BaseBackend.execution_device` class attribute
        # (src.features.backends.base_backend.ExecutionDevice) - set
        # explicitly per fixture backend, never derived from `driver`, the
        # same way a real backend class declares it rather than the config
        # inferring it from a name.
        self.execution_device = execution_device


class _FakeBackendConfigStore:
    def __init__(self, configs_by_id, default_id=None):
        self._configs_by_id = configs_by_id
        self.default_id = default_id

    def get_default_backend(self, engine):
        if self.default_id is None:
            return None
        config = self._configs_by_id.get(self.default_id)
        return config if config and config.engine == engine else None


class _FakeBackendRegistry:
    def __init__(self, backends, default_id=None):
        self._backends = backends
        self.backend_config_store = _FakeBackendConfigStore(
            {b.config.id: b.config for b in backends}, default_id=default_id
        )

    def get_backends_for_engine(self, engine):
        return [b for b in self._backends if b.engine == engine]

    def get_backend(self, backend_id):
        return next((b for b in self._backends if b.backend_id == backend_id), None)


def _two_comfyui_backends():
    return [
        _FakeBackend(_FakeBackendConfig("comfy-a", "Comfy A", "comfyui")),
        _FakeBackend(_FakeBackendConfig("comfy-b", "Comfy B", "comfyui")),
    ]


class TestGetPresetRequirementsMultiBackend:
    """`fixture_backend_check` is "ok" on `comfy-a`, "missing" on `comfy-b` -
    exercises per-backend evaluation, the chosen-backend resolution order
    (requested > default > best-scoring), and the `backends` listing."""

    def setup_method(self):
        requirement_checker_registry.register(RequirementCheckerRegistration(
            type_name="fixture_backend_check", checker=_FixtureBackendChecker("comfy-a"), source="test-multi-backend",
        ))

    def teardown_method(self):
        requirement_checker_registry.unregister_source("test-multi-backend")

    def _preset_and_collaborators(self, default_id=None):
        preset = PresetTemplate(
            id="comfy-preset", name="Preset", version="1.0.0", path="/tmp/preset", modes={},
            engine="comfyui", requirements=[{"type": "fixture_backend_check"}],
        )
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            backend_registry=_FakeBackendRegistry(_two_comfyui_backends(), default_id=default_id),
            requirements_cache=RequirementsCache(),
        )
        return preset, collaborators

    @pytest.mark.asyncio
    async def test_no_default_chooses_the_best_scoring_backend(self):
        _, collaborators = self._preset_and_collaborators(default_id=None)

        data = await operations.get_preset_requirements(collaborators, "comfy-preset")

        assert data["summary"] == {"ok": 1, "missing": 0, "unknown": 0, "optional_missing": 0}
        assert data["results"][0]["backend_id"] == "comfy-a"
        assert {b["id"] for b in data["backends"]} == {"comfy-a", "comfy-b"}
        by_id = {b["id"]: b for b in data["backends"]}
        assert by_id["comfy-a"]["summary"]["ok"] == 1
        assert by_id["comfy-b"]["summary"]["missing"] == 1

    @pytest.mark.asyncio
    async def test_default_backend_wins_over_best_scoring(self):
        _, collaborators = self._preset_and_collaborators(default_id="comfy-b")

        data = await operations.get_preset_requirements(collaborators, "comfy-preset")

        assert data["summary"] == {"ok": 0, "missing": 1, "unknown": 0, "optional_missing": 0}
        assert data["results"][0]["backend_id"] == "comfy-b"
        by_id = {b["id"]: b for b in data["backends"]}
        assert by_id["comfy-b"]["is_default"] is True
        assert by_id["comfy-a"]["is_default"] is False

    @pytest.mark.asyncio
    async def test_requested_backend_id_wins_over_default(self):
        _, collaborators = self._preset_and_collaborators(default_id="comfy-b")

        data = await operations.get_preset_requirements(collaborators, "comfy-preset", backend_id="comfy-a")

        assert data["summary"]["ok"] == 1
        assert data["results"][0]["backend_id"] == "comfy-a"

    @pytest.mark.asyncio
    async def test_unknown_requested_backend_id_falls_back_to_default(self):
        _, collaborators = self._preset_and_collaborators(default_id="comfy-b")

        data = await operations.get_preset_requirements(collaborators, "comfy-preset", backend_id="does-not-exist")

        assert data["results"][0]["backend_id"] == "comfy-b"


class _FakeGpuMonitor:
    def __init__(self, total_vram_mb: int, available: bool = True):
        self.available = available
        self._total_vram_mb = total_vram_mb

    def get_total_vram(self) -> int:
        return self._total_vram_mb


def _native_backends():
    return [
        _FakeBackend(
            _FakeBackendConfig("native-local", "Local GPU", "native", driver="native"),
            execution_device="this_host_gpu",
        ),
        _FakeBackend(
            _FakeBackendConfig("native-remote-1", "Remote Worker", "native", driver="native.remote"),
            execution_device="remote",
        ),
    ]


class TestGetPresetRequirementsVramMinGbPerBackend:
    """`vram_min_gb` is backend-scoped, so a local backend's VRAM
    reading and a remote worker's (always "unknown" - no local reading
    applies to hardware this process cannot see) must be evaluated and
    reported independently, never merged into one shared verdict. Uses the
    REAL `VramMinGbRequirementChecker` (seeded by `setup_module`), not a
    fixture checker."""

    def _preset_and_collaborators(self, gpu_total_gb, default_id=None, gb=16):
        preset = _preset(
            preset_id="native-preset", requirements=[{"type": "vram_min_gb", "gb": gb}],
        )
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            backend_registry=_FakeBackendRegistry(_native_backends(), default_id=default_id),
            gpu_monitor=_FakeGpuMonitor(int(gpu_total_gb * 1024)),
            requirements_cache=RequirementsCache(),
        )
        return preset, collaborators

    @pytest.mark.asyncio
    async def test_low_vram_local_default_is_missing_remote_is_unknown(self):
        _, collaborators = self._preset_and_collaborators(gpu_total_gb=8, default_id="native-local")

        data = await operations.get_preset_requirements(collaborators, "native-preset")

        assert data["results"][0]["status"] == "missing"
        assert data["results"][0]["backend_id"] == "native-local"
        by_id = {b["id"]: b for b in data["backends"]}
        assert by_id["native-local"]["summary"] == {"ok": 0, "missing": 1, "unknown": 0, "optional_missing": 0}
        assert by_id["native-remote-1"]["summary"] == {"ok": 0, "missing": 0, "unknown": 1, "optional_missing": 0}

    @pytest.mark.asyncio
    async def test_low_vram_remote_default_does_not_inherit_locals_missing_verdict(self):
        """The bug this guards: `vram_min_gb` used to be host-scoped,
        so its ONE verdict came from whichever backend `build_requirement_context`
        picked as default and was then copied onto every other candidate -
        picking the remote backend as default must read "unknown" for it,
        never the local host's "missing"."""
        _, collaborators = self._preset_and_collaborators(gpu_total_gb=8, default_id="native-remote-1")

        data = await operations.get_preset_requirements(collaborators, "native-preset")

        assert data["results"][0]["status"] == "unknown"
        assert data["results"][0]["backend_id"] == "native-remote-1"
        by_id = {b["id"]: b for b in data["backends"]}
        assert by_id["native-local"]["summary"]["missing"] == 1

    @pytest.mark.asyncio
    async def test_high_vram_local_is_ok_remote_stays_unknown(self):
        _, collaborators = self._preset_and_collaborators(gpu_total_gb=24, default_id="native-local")

        data = await operations.get_preset_requirements(collaborators, "native-preset")

        assert data["results"][0]["status"] == "ok"
        by_id = {b["id"]: b for b in data["backends"]}
        assert by_id["native-remote-1"]["summary"]["unknown"] == 1

    @pytest.mark.asyncio
    async def test_explicit_backend_id_selects_that_backends_own_verdict(self):
        _, collaborators = self._preset_and_collaborators(gpu_total_gb=8, default_id="native-remote-1")

        data = await operations.get_preset_requirements(collaborators, "native-preset", backend_id="native-local")

        assert data["results"][0]["status"] == "missing"
        assert data["results"][0]["backend_id"] == "native-local"

    @pytest.mark.asyncio
    async def test_cached_read_returns_the_same_per_candidate_results(self):
        _, collaborators = self._preset_and_collaborators(gpu_total_gb=8, default_id="native-local")

        first = await operations.get_preset_requirements(collaborators, "native-preset")
        second = await operations.get_preset_requirements(collaborators, "native-preset")

        assert first["results"] == second["results"]
        assert first["backends"] == second["backends"]

    @pytest.mark.asyncio
    async def test_refresh_reevaluates_every_candidate(self):
        _, collaborators = self._preset_and_collaborators(gpu_total_gb=8, default_id="native-local")

        first = await operations.get_preset_requirements(collaborators, "native-preset")
        second = await operations.get_preset_requirements(collaborators, "native-preset", refresh=True)

        assert first["results"][0]["status"] == second["results"][0]["status"] == "missing"
        assert second["checked_at"] >= first["checked_at"]

    @pytest.mark.asyncio
    async def test_no_backend_registered_yields_explicit_unknown_not_a_local_guess(self):
        preset = _preset(preset_id="native-preset-orphan", requirements=[{"type": "vram_min_gb", "gb": 16}])
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            backend_registry=_FakeBackendRegistry([], default_id=None),
            gpu_monitor=_FakeGpuMonitor(8 * 1024),
            requirements_cache=RequirementsCache(),
        )

        data = await operations.get_preset_requirements(collaborators, "native-preset-orphan")

        assert data["results"][0]["status"] == "unknown"
        assert data["backends"] == []

    @pytest.mark.asyncio
    async def test_host_scoped_entry_alongside_vram_is_still_evaluated_once(self):
        """A mixed requirements list: `platform` (host-scoped) must still be
        shared across both backends' summaries while `vram_min_gb`
        (backend-scoped) differs between them."""
        preset = _preset(preset_id="native-preset-mixed", requirements=[
            {"type": "platform", "os": ["linux", "darwin", "windows"]},
            {"type": "vram_min_gb", "gb": 16},
        ])
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            backend_registry=_FakeBackendRegistry(_native_backends(), default_id="native-local"),
            gpu_monitor=_FakeGpuMonitor(8 * 1024),
            requirements_cache=RequirementsCache(),
        )

        data = await operations.get_preset_requirements(collaborators, "native-preset-mixed")

        by_id = {b["id"]: b for b in data["backends"]}
        assert by_id["native-local"]["summary"] == {"ok": 1, "missing": 1, "unknown": 0, "optional_missing": 0}
        assert by_id["native-remote-1"]["summary"] == {"ok": 1, "missing": 0, "unknown": 1, "optional_missing": 0}


class _ComfyUIShapedConfig:
    """Mirrors `ComfyUIBackendConfig`'s actual shape enough for this
    fixture (a plain network `host`, `driver` defaulting to the engine
    name) without importing content/plugins/marketplace/comfyui-backend -
    that plugin keeps its own tests/ dir per the marketplace convention."""

    def __init__(self, id, name, host):
        self.id = id
        self.name = name
        self.engine = "comfyui"
        self.driver = "comfyui"
        self.host = host


class _ComfyUIShapedBackend(InProcessBackend):
    """The real plugin-registration shape (docs/backends.md "Contributing
    an engine from a plugin"): a REAL `InProcessBackend` subclass, driver ==
    engine == "comfyui", talking to a configurable network `host` - and
    deliberately no `execution_device` override, exactly like the actual
    `comfyui-backend` plugin's `ComfyUIBackend`, so it inherits
    `BaseBackend`'s "unestablished" default rather than a test double
    faking one. `health_check`/`get_system_info` are irrelevant here - just
    enough to satisfy `BaseBackend`'s abstract contract."""

    async def health_check(self):
        return {"status": "healthy"}

    async def get_system_info(self):
        return {}


class _ComfyUIShapedRegistry:
    """Minimal `BackendRegistry` stand-in wired to one real
    `_ComfyUIShapedBackend` instance (as opposed to `_FakeBackend`, which
    fabricates a plain object with a hand-set `execution_device`) - proves
    the fix reads `execution_device` off the real backend class hierarchy."""

    def __init__(self, backend, default_id=None):
        self._backend = backend
        self.backend_config_store = _FakeBackendConfigStore(
            {backend.config.id: backend.config}, default_id=default_id
        )

    def get_backends_for_engine(self, engine):
        return [self._backend] if self._backend.engine == engine else []

    def get_backend(self, backend_id):
        return self._backend if self._backend.config.id == backend_id else None


class TestVramMinGbAgainstRealComfyUIShapedBackend:
    """A `comfyui`-engine backend pointed at a network host (e.g.
    "gpu-worker") must read `vram_min_gb` as `unknown` regardless of this
    API host's own VRAM - the exact bug a driver-name substring check
    produced (see the module message this rework responds to)."""

    def _collaborators_for(self, gpu_total_gb):
        preset = _preset(preset_id="comfy-worker-preset", requirements=[{"type": "vram_min_gb", "gb": 16}])
        preset.engine = "comfyui"
        backend = _ComfyUIShapedBackend(_ComfyUIShapedConfig("comfy-worker", "GPU Worker", host="gpu-worker"))
        collaborators = _collaborators(
            file_repo=MagicMock(find_preset_by_id=MagicMock(return_value=preset)),
            backend_registry=_ComfyUIShapedRegistry(backend, default_id="comfy-worker"),
            gpu_monitor=_FakeGpuMonitor(int(gpu_total_gb * 1024)),
            requirements_cache=RequirementsCache(),
        )
        return preset, collaborators

    @pytest.mark.asyncio
    async def test_unknown_on_an_8gb_api_host(self):
        _, collaborators = self._collaborators_for(gpu_total_gb=8)

        data = await operations.get_preset_requirements(collaborators, "comfy-worker-preset")

        assert data["results"][0]["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_unknown_on_a_24gb_api_host(self):
        """Before this rework, a driver name that merely didn't contain
        "remote" was read as local - a beefy API host would have made a
        genuinely remote ComfyUI worker's floor read "ok" for hardware it
        never touched."""
        _, collaborators = self._collaborators_for(gpu_total_gb=24)

        data = await operations.get_preset_requirements(collaborators, "comfy-worker-preset")

        assert data["results"][0]["status"] == "unknown"
