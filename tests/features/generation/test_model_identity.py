"""
`resolve_model_identity` is the pure algorithm behind the "fair" scheduling
policy's model-affinity key (`GenerationOrchestrator._resolve_model_key`,
docs/backends.md "Scheduling policy"). These tests hand-build processed pipe
dicts shaped like Wan's dual-loader pipeline (see
`content/presets/marketplace/Wan/modes/video/pipeline.yml`) to pin the exact
counterexample that motivated it: a preset can carry a pipe that echoes the
same `model:<id>` references purely for tracking/display regardless of
whether the loader that would really load them is enabled, and a naive walk
of every active pipe must not let that leak into the key.
"""

from typing import Any, Dict, Optional

from src.features.generation.model_identity import resolve_model_identity
from src.features.models.records import Model


def _ref(model_id: str) -> str:
    return f"model:{model_id}"


def _loader_pipe(
    pipe_id: str,
    *,
    family: str = "model_loader/wan22",
    enabled: bool = True,
    high: str = "high-checkpoint",
    low: Optional[str] = None,
    text_encoder: str = "shared-te",
    vae: str = "shared-vae",
    extra_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config: Dict[str, Any] = {
        "high_noise_model": {"file_path": _ref(high), "name": _ref(high)},
        "low_noise_model": {"file_path": _ref(low) if low else "", "name": _ref(low) if low else ""},
        "text_encoder": {"file_path": _ref(text_encoder), "name": _ref(text_encoder)},
        "vae": {"file_path": _ref(vae), "name": _ref(vae)},
    }
    if extra_config:
        config.update(extra_config)
    return {"id": pipe_id, "name": family, "enabled": enabled, "config": config}


def _param_emitter_pipe(*model_ids: str) -> Dict[str, Any]:
    """Wan's always-on `param_emitter`: lists every model ref as a display
    tuple regardless of which loader is actually enabled - the exact pipe
    that must never contribute to the affinity key."""
    return {
        "id": "param_emitter",
        "name": "param_emitter",
        "enabled": True,
        "config": {"parameters": [["model", _ref(mid)] for mid in model_ids]},
    }


def _registry(**types: str) -> Any:
    """A lookup_model callable backed by an in-memory {id: model_type} map.
    Every id gets a distinct sha256 derived from its id, so identity
    differences are easy to reason about."""
    records = {
        model_id: Model(id=model_id, model_type=model_type, sha256=f"sha-{model_id}")
        for model_id, model_type in types.items()
    }

    def _lookup(model_id: str) -> Optional[Model]:
        return records.get(model_id)

    return _lookup


class TestSameHighDifferentLow:
    def test_different_low_expert_changes_the_key(self):
        lookup = _registry(
            t2v_high="checkpoint", t2v_low_a="checkpoint", t2v_low_b="checkpoint",
            te="text_encoder", vae="vae",
        )
        pipes_a = [_loader_pipe("t2v_loader", high="t2v_high", low="t2v_low_a", text_encoder="te", vae="vae")]
        pipes_b = [_loader_pipe("t2v_loader", high="t2v_high", low="t2v_low_b", text_encoder="te", vae="vae")]

        key_a = resolve_model_identity(pipes_a, lookup)
        key_b = resolve_model_identity(pipes_b, lookup)

        assert key_a is not None and key_b is not None
        assert key_a != key_b


class TestInactiveLoaderNeverLeaks:
    def test_two_i2v_jobs_with_different_unused_t2v_selections_share_a_key(self):
        lookup = _registry(
            t2v_high_a="checkpoint", t2v_high_b="checkpoint",
            i2v_high="checkpoint", te="text_encoder", vae="vae",
        )
        pipes_a = [
            _loader_pipe("t2v_loader", enabled=False, high="t2v_high_a", text_encoder="te", vae="vae"),
            _loader_pipe("i2v_loader", enabled=True, high="i2v_high", text_encoder="te", vae="vae"),
        ]
        pipes_b = [
            _loader_pipe("t2v_loader", enabled=False, high="t2v_high_b", text_encoder="te", vae="vae"),
            _loader_pipe("i2v_loader", enabled=True, high="i2v_high", text_encoder="te", vae="vae"),
        ]

        assert resolve_model_identity(pipes_a, lookup) == resolve_model_identity(pipes_b, lookup)

    def test_needing_both_sets_differs_from_needing_one(self):
        lookup = _registry(t2v_high="checkpoint", i2v_high="checkpoint", te="text_encoder", vae="vae")
        pipes_both = [
            _loader_pipe("t2v_loader", enabled=True, high="t2v_high", text_encoder="te", vae="vae"),
            _loader_pipe("i2v_loader", enabled=True, high="i2v_high", text_encoder="te", vae="vae"),
        ]
        pipes_i2v_only = [
            _loader_pipe("t2v_loader", enabled=False, high="t2v_high", text_encoder="te", vae="vae"),
            _loader_pipe("i2v_loader", enabled=True, high="i2v_high", text_encoder="te", vae="vae"),
        ]

        key_both = resolve_model_identity(pipes_both, lookup)
        key_i2v_only = resolve_model_identity(pipes_i2v_only, lookup)

        assert key_both is not None and key_i2v_only is not None
        assert key_both != key_i2v_only

    def test_param_emitter_never_contributes_even_when_active(self):
        """`param_emitter` is always enabled and lists BOTH loaders' refs
        unconditionally; only the loader pipes' own `enabled` gate may decide
        what counts."""
        lookup = _registry(t2v_high="checkpoint", i2v_high="checkpoint", te="text_encoder", vae="vae")
        pipes_i2v_only = [
            _loader_pipe("t2v_loader", enabled=False, high="t2v_high", text_encoder="te", vae="vae"),
            _loader_pipe("i2v_loader", enabled=True, high="i2v_high", text_encoder="te", vae="vae"),
            _param_emitter_pipe("t2v_high", "i2v_high", "te", "vae"),
        ]
        pipes_i2v_only_no_emitter = pipes_i2v_only[:2]

        assert resolve_model_identity(pipes_i2v_only, lookup) == resolve_model_identity(
            pipes_i2v_only_no_emitter, lookup
        )


class TestIdenticalResolvedSetControl:
    def test_two_independently_built_but_equal_pipe_lists_share_a_key(self):
        lookup = _registry(high="checkpoint", low="checkpoint", te="text_encoder", vae="vae")
        pipes_a = [_loader_pipe("t2v_loader", high="high", low="low", text_encoder="te", vae="vae")]
        pipes_b = [_loader_pipe("t2v_loader", high="high", low="low", text_encoder="te", vae="vae")]

        assert pipes_a is not pipes_b
        key = resolve_model_identity(pipes_a, lookup)
        assert key is not None
        assert key == resolve_model_identity(pipes_b, lookup)


class TestUnknownIdentityDisablesAffinity:
    def test_unresolvable_reference_returns_none(self):
        lookup = _registry(te="text_encoder", vae="vae")  # "missing" is absent
        pipes = [_loader_pipe("t2v_loader", high="missing", text_encoder="te", vae="vae")]

        assert resolve_model_identity(pipes, lookup) is None

    def test_no_checkpoint_class_reference_returns_none(self):
        lookup = _registry(te="text_encoder", vae="vae")
        pipes = [
            {
                "id": "t2v_loader",
                "name": "model_loader/wan22",
                "enabled": True,
                "config": {
                    "text_encoder": {"file_path": _ref("te")},
                    "vae": {"file_path": _ref("vae")},
                },
            }
        ]

        assert resolve_model_identity(pipes, lookup) is None

    def test_no_loader_pipes_at_all_returns_none(self):
        lookup = _registry(high="checkpoint")
        pipes = [_param_emitter_pipe("high")]

        assert resolve_model_identity(pipes, lookup) is None

    def test_disabled_loader_pipe_contributes_nothing(self):
        lookup = _registry(high="checkpoint")
        pipes = [_loader_pipe("t2v_loader", enabled=False, high="high")]

        assert resolve_model_identity(pipes, lookup) is None


class TestLoadRelevantSettings:
    def test_different_dtype_on_the_same_checkpoint_changes_the_key(self):
        lookup = _registry(high="checkpoint", te="text_encoder", vae="vae")
        pipes_bf16 = [
            _loader_pipe(
                "t2v_loader", high="high", text_encoder="te", vae="vae",
                extra_config={"dtype": "bfloat16"},
            )
        ]
        pipes_fp8 = [
            _loader_pipe(
                "t2v_loader", high="high", text_encoder="te", vae="vae",
                extra_config={"dtype": "fp8"},
            )
        ]

        key_bf16 = resolve_model_identity(pipes_bf16, lookup)
        key_fp8 = resolve_model_identity(pipes_fp8, lookup)

        assert key_bf16 is not None and key_fp8 is not None
        assert key_bf16 != key_fp8

    def test_different_quantization_setting_changes_the_key(self):
        lookup = _registry(high="checkpoint", te="text_encoder", vae="vae")
        pipes_a = [
            _loader_pipe(
                "t2v_loader", high="high", text_encoder="te", vae="vae",
                extra_config={"quantization": "nvfp4"},
            )
        ]
        pipes_b = [
            _loader_pipe(
                "t2v_loader", high="high", text_encoder="te", vae="vae",
                extra_config={"quantization": "fp8"},
            )
        ]

        assert resolve_model_identity(pipes_a, lookup) != resolve_model_identity(pipes_b, lookup)

    def test_checkpoint_loader_family_also_counts(self):
        """SDXL's single-file loader uses `checkpoint_loader/*`, not
        `model_loader/*` - both families map to "Loading model" in
        `PIPE_FAMILY_TITLES` and both must be trusted."""
        lookup = _registry(sdxl="checkpoint")
        pipes = [
            {
                "id": "checkpoint_loader",
                "name": "checkpoint_loader/sdxl",
                "enabled": True,
                "config": {"model": {"file_path": _ref("sdxl"), "name": _ref("sdxl")}},
            }
        ]

        assert resolve_model_identity(pipes, lookup) is not None
