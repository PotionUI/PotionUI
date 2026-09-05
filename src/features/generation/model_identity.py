"""
Model identity: the load-compatibility signature the "fair" scheduling policy's
model-affinity check compares two queued generations against (see
`docs/backends.md` "Scheduling policy"). A pure function over an already-built
pipeline's processed pipes, so it is testable without a running orchestrator,
database, or model index.
"""

from typing import Any, Callable, Dict, List, Optional

from src.features.models.form_refs import collect_model_ids

# The depot taxonomy's model types that name a base/checkpoint weight file a
# native or ComfyUI loader actually loads as THE model - as opposed to a LoRA,
# VAE, text encoder, ControlNet or other auxiliary reference that rides
# alongside it. See `src.platform.filesystem.model_types`.
_BASE_MODEL_TYPES = frozenset({"checkpoint", "diffusion_model", "unet"})

# Pipe families whose config is what actually gets handed to `MODELS.acquire()`
# - the same grouping `PIPE_FAMILY_TITLES` in `src/pipelines/contracts.py` uses
# for the "Loading model" display title. A pipe outside this set can carry the
# exact same `model:<id>` references purely for tracking/display regardless of
# whether the loader that would really load them is enabled: Wan's
# `param_emitter` is always enabled and unconditionally lists all four Wan22
# expert refs even when only one loader's `enabled:` gate is true, so trusting
# any active pipe's config would make an unused, disabled selection leak into
# the signature. Only these families are trusted to say what will actually load.
_LOADER_PIPE_FAMILIES = frozenset({"model_loader", "checkpoint_loader"})

ModelLookup = Callable[[str], Optional[Any]]


def resolve_model_identity(pipes: List[Dict[str, Any]], lookup_model: ModelLookup) -> Optional[str]:
    """The conservative load-compatibility signature for a built pipeline, or
    `None` when one can't be derived with confidence.

    Only pipes that are both active (`processed_pipe['enabled']`, already
    resolved to a real bool by `PresetProcessor`) and belong to a loader
    family (`_LOADER_PIPE_FAMILIES`) contribute. Each such pipe's whole
    `config` is walked with `collect_model_ids` - the same generic
    `model:<id>` walk model-access enforcement uses - and every id found is
    looked up. A single id that fails to resolve makes the whole signature
    unreliable enough to drop rather than derive from a partial view, so the
    function returns `None` immediately. Among ids that do resolve, only
    checkpoint-class ones (`_BASE_MODEL_TYPES`) become identity, keyed by
    digest when hashed else by id; a LoRA, VAE, text encoder or ControlNet
    reference must still resolve to count as "known", but never contributes
    identity itself. Two requests are affinity-compatible when their active
    loader pipes resolve to the same checkpoint set AND the same
    load-relevant settings those loader configs expose (`dtype`, any
    `quant*`-named key) - both folded into one deterministic string. No
    checkpoint-class reference among the active loader pipes also returns
    `None`: affinity is a hint that only fires on a real match, never a
    fallback to "probably the same".
    """
    identities = set()
    settings = set()
    resolved_cache: Dict[str, Optional[Any]] = {}

    for pipe in pipes:
        if not pipe.get("enabled"):
            continue
        name = pipe.get("name") or ""
        family = name.split("/", 1)[0]
        if family not in _LOADER_PIPE_FAMILIES:
            continue

        config = pipe.get("config") or {}

        for model_id in collect_model_ids(config):
            if model_id not in resolved_cache:
                resolved_cache[model_id] = lookup_model(model_id)
            model = resolved_cache[model_id]
            if model is None:
                return None
            if model.model_type in _BASE_MODEL_TYPES:
                identities.add(model.sha256 or model_id)

        pipe_key = pipe.get("id") or name
        for key, value in config.items():
            if key == "dtype" or key.lower().startswith("quant"):
                settings.add(f"{pipe_key}.{key}={value}")

    if not identities:
        return None

    return "|".join(sorted(identities) + sorted(settings))
