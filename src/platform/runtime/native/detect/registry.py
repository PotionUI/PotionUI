"""Arch registry: resolves a detected DiT config to a concrete model.

A ``ModelSpec`` binds a detection signature to everything the loader and the
sampler need: which arch class to build, the flow-matching sampling settings,
the latent format, which text encoders / VAE to pair, the load-integrity key
allowlists, and a memory cost estimate.

Specs live on the ``arch_registry`` singleton keyed by ``(family, variant)``.
The vendored specs in this module self-register at import; a provider shipping
its own implementation of a family registers at a higher priority to take the
key over.

``model_class`` and ``state_dict_map`` are lazy ``"module.path:name"`` strings
resolved through importlib on first use, so registering a spec never imports
the (heavy, and possibly not-yet-written) arch module. The boot-import guard
(``tests/architecture/test_boot_imports.py``) depends on that laziness.
"""

from __future__ import annotations

import fnmatch
import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..errors import NativeEngineUnsupportedError

logger = logging.getLogger(__name__)

VENDORED = "vendored"


class DuplicateArchSpecError(ValueError):
    """Raised when two specs claim one (family, variant) at the same priority."""


@dataclass(frozen=True)
class ModelSpec:
    """Static description of one supported diffusion model variant."""

    family: str
    variant: str
    # Subset of a detected unet config that must match for this spec to apply.
    signature: dict[str, Any]
    # "module.path:ClassName" — resolved lazily via importlib.
    model_class: str
    sampling_settings: dict[str, Any] = field(default_factory=dict)
    latent_format: dict[str, Any] = field(default_factory=dict)
    clip_targets: list[str] = field(default_factory=list)
    vae_target: str = ""
    # fnmatch glob patterns tolerated during strict-ish state-dict load.
    expected_missing_keys: set[str] = field(default_factory=set)
    expected_unexpected_keys: set[str] = field(default_factory=set)
    memory_cost_gb: float = 0.0
    # "module.path:callable" mapping checkpoint keys to this arch's own module
    # names. None means the checkpoint keys are already this arch's names.
    state_dict_map: str | None = None

    def matches(self, config: dict[str, Any]) -> bool:
        """True when every key/value in ``signature`` is present in ``config``."""
        return all(config.get(k) == v for k, v in self.signature.items())

    def resolve_model_class(self) -> type:
        """Import and return the arch class named by ``model_class``."""
        return _resolve_dotted(self.model_class, "ModelSpec.model_class")

    def resolve_state_dict_map(self) -> Callable[[dict[str, Any]], dict[str, Any]] | None:
        """Import and return the key remapper, or None when the spec declares none."""
        if self.state_dict_map is None:
            return None
        return _resolve_dotted(self.state_dict_map, "ModelSpec.state_dict_map")

    def key_is_expected_missing(self, key: str) -> bool:
        return any(fnmatch.fnmatch(key, pat) for pat in self.expected_missing_keys)

    def key_is_expected_unexpected(self, key: str) -> bool:
        return any(fnmatch.fnmatch(key, pat) for pat in self.expected_unexpected_keys)


def _resolve_dotted(target: str, what: str) -> Any:
    if ":" not in target:
        raise NativeEngineUnsupportedError(
            f"{what} must be 'module:Class', got '{target}'"
        )
    module_path, _, attr = target.partition(":")
    return getattr(importlib.import_module(module_path), attr)


@dataclass(frozen=True)
class ArchRegistration:
    """One spec's registration: who provided it and at what priority."""

    spec: ModelSpec
    provider: str
    priority: int


class ArchRegistry:
    """Registry mapping (family, variant) -> the ModelSpec that implements it."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], ArchRegistration] = {}

    def register(
        self, spec: ModelSpec, provider: str = VENDORED, priority: int = 0
    ) -> None:
        """Register ``spec``. Highest priority wins; a priority tie is an error."""
        key = (spec.family, spec.variant)
        existing = self._by_key.get(key)
        if existing is not None:
            if priority == existing.priority:
                raise DuplicateArchSpecError(
                    f"arch spec {key[0]}/{key[1]} already registered by "
                    f"'{existing.provider}' at priority {priority}"
                )
            if priority < existing.priority:
                return
            logger.debug(
                "arch spec %s/%s: '%s' (p%d) overrides '%s' (p%d)",
                *key, provider, priority, existing.provider, existing.priority,
            )
        self._by_key[key] = ArchRegistration(spec, provider, priority)

    def unregister_provider(self, provider: str) -> None:
        """Remove every spec registered by ``provider``."""
        for key in [k for k, reg in self._by_key.items() if reg.provider == provider]:
            del self._by_key[key]

    def get(self, family: str, variant: str) -> ModelSpec | None:
        reg = self._by_key.get((family, variant))
        return reg.spec if reg is not None else None

    def all(self) -> list[ModelSpec]:
        """Every registered spec, in registration order."""
        return [reg.spec for reg in self._by_key.values()]

    def registrations(self) -> list[ArchRegistration]:
        return list(self._by_key.values())

    def match(self, config: dict[str, Any]) -> ModelSpec:
        """Return the first registered spec whose signature matches ``config``."""
        for spec in self.all():
            if spec.matches(config):
                logger.debug("matched ModelSpec %s/%s", spec.family, spec.variant)
                return spec
        raise NativeEngineUnsupportedError(
            f"no ModelSpec matches detected config: {config!r}"
        )


arch_registry = ArchRegistry()


# Non-weight keys any checkpoint may carry without failing the integrity gate:
# quantisation sidecar tensors, plus the sampling-schedule buffer ComfyUI embeds
# when a checkpoint is saved through a ComfyUI workflow (common on civitai
# merges) - metadata about sampling, not model weights, safe to drop on load.
_SIDECAR_GLOBS = {
    "*.weight_scale", "*.input_scale", "*.scale_weight", "scaled_fp8",
    "model_sampling.*",
}


_VENDORED_SPECS: list[ModelSpec] = [
    ModelSpec(
        family="flux",
        variant="flux1",
        signature={"image_model": "flux"},
        model_class="src.platform.runtime.native.arch.flux.model:Flux",
        sampling_settings={
            "prediction": "const",          # flow-matching CONST velocity
            "shift": 1.15,                  # dynamic shift resolved at sample time
            "base_shift": 0.5,
            "max_shift": 1.15,
            "guidance": "embedded",         # distilled guidance, not CFG
        },
        latent_format={
            "latent_channels": 16,
            "scale_factor": 0.3611,
            "shift_factor": 0.1159,
        },
        clip_targets=["t5xxl", "clip_l"],
        vae_target="flux_ae",
        expected_unexpected_keys=set(_SIDECAR_GLOBS),
        memory_cost_gb=24.0,
    ),
    ModelSpec(
        family="flux",
        variant="flux2",
        signature={"image_model": "flux2"},
        model_class="src.platform.runtime.native.arch.flux.model:Flux",
        sampling_settings={
            "prediction": "const",
            "shift": 2.02,
            "guidance": "embedded",
        },
        latent_format={
            "latent_channels": 32,
            "scale_factor": 1.0,            # confirmed against flux2-vae by VAE slice
            "shift_factor": 0.0,
        },
        clip_targets=["qwen3"],
        vae_target="flux2_ae",
        expected_unexpected_keys=set(_SIDECAR_GLOBS),
        memory_cost_gb=18.0,
    ),
    ModelSpec(
        family="krea2",
        variant="krea2_turbo",
        signature={"image_model": "krea2"},
        model_class="src.platform.runtime.native.arch.krea2.model:Krea2",
        sampling_settings={
            # Flow-matching (CONST) with a FIXED mu -- NOT the resolution-dynamic
            # interpolation. Upstream's own sampling.py (krea-ai/krea-2, timesteps())
            # documents the dynamic mu=slope*seq_len+intercept (anchored at
            # y1=0.5@x1=(256/16)^2, y2=1.15@x2=(1280/16)^2) as the *base/midtrain*
            # checkpoint's schedule, but says explicitly: "Pass an explicit `mu` to
            # pin a constant shift regardless of resolution (used by the distilled
            # checkpoint, which was trained at a fixed mu=1.15)." diffusers'
            # Krea2Pipeline (pipeline_krea2.py) confirms it structurally:
            # `if self.config.is_distilled: mu = 1.15 else: mu = calculate_shift(...)`.
            # Turbo IS the distilled checkpoint, so it gets the fixed value by
            # default. One spec covers turbo + a raw/base checkpoint (same
            # architecture, no distinguishing signature) -- the Krea-2 preset's
            # speed profile picks the regime at generation time: "base" requests
            # the resolution-anchored dynamic-mu interpolation via the
            # `fixed_mu`/`dynamic_shift` whitelist in `engine._sampling_settings_for`
            # (x1_px=256/x2_px=1280/align=16 anchors, same as upstream's own
            # midtrain-checkpoint schedule); every other profile leaves this
            # ModelSpec's fixed_mu untouched. See generator/krea2/main.py's
            # `mu_schedule` config knob.
            "prediction": "const",
            "fixed_mu": 1.15,
            "default_steps": 8,             # turbo (distilled); raw uses ~52
            # True CFG (BE-CFG-KREA2, was "none"): turbo drives cfg_scale=1.0, which
            # makes TrueCFG collapse to a single conditional-only forward -- see
            # cfg.py's `abs(scale - 1.0) < 1e-6 or uncond is None` short-circuit --
            # byte-identical to the old NoCFG strategy's single forward. A raw/base
            # checkpoint (or an experiment on the distilled checkpoint) sets
            # cfg_scale > 1.0 via the preset and gets a real negative-conditioned
            # pass. Mirrors Z-Image's "one spec, cfg_scale picks the regime" design
            # (see this file's z_image entry).
            "guidance": "cfg",
        },
        latent_format={
            # Qwen-Image VAE is the Wan 2.1 causal 3D VAE verbatim (see
            # src/platform/runtime/native/vae/causal_3d.py); its latent format is
            # ComfyUI's Wan21 (comfy/latent_formats.py) -- per-channel, not
            # scalar. scale_factor is 1.0 (folded into latents_std) so
            # normalization is exactly (latent - mean) / std. Values also
            # exported as vae.causal_3d.LATENTS_MEAN/LATENTS_STD -- keep both
            # in sync if either changes.
            "latent_channels": 16,
            "scale_factor": 1.0,
            "latents_mean": [
                -0.7571, -0.7089, -0.9113, 0.1075, -0.1745, 0.9653, -0.1517, 1.5508,
                0.4134, -0.0715, 0.5517, -0.3632, -0.1922, -0.9497, 0.2503, -0.2921,
            ],
            "latents_std": [
                2.8184, 1.4541, 2.3275, 2.6558, 1.2196, 1.7708, 2.6052, 2.0743,
                3.2687, 2.1526, 2.8652, 1.5579, 1.6382, 1.1253, 2.8251, 1.9160,
            ],
        },
        clip_targets=["qwen3vl_4b"],
        vae_target="qwen_image",
        # Mixed-dtype checkpoint verified: bf16 block/txtfusion Linears + f32
        # norms/mods/peripherals; a clean checkpoint has no missing/unexpected
        # keys, but resaved/quantised distributions may carry sidecar keys.
        expected_unexpected_keys=set(_SIDECAR_GLOBS),
        memory_cost_gb=26.3,
    ),
    ModelSpec(
        family="qwen_image",
        variant="qwen_image",
        signature={"image_model": "qwen_image"},
        model_class="src.platform.runtime.native.arch.qwen_image.model:QwenImageDiT",
        sampling_settings={
            "prediction": "const",          # flow-matching CONST (ModelType.FLUX)
            "shift": 1.15,                  # ComfyUI QwenImage
            "guidance": "cfg",              # true CFG (cond/uncond); denoise's canonical mode name
        },
        latent_format={
            # Wan 2.1 VAE latent (16ch); scale/shift live in the VAE (per-channel).
            "latent_channels": 16,
            "format": "wan21",
        },
        clip_targets=["qwen25_vl_7b"],
        vae_target="qwen_image",
        # 2512 is BARE fp8 (no sidecars). The 2511/edit variant is fp8-scaled with
        # scale_weight/scale_input + comfy_quant markers — allowlist all spellings
        # so neither variant trips the integrity gate.
        expected_unexpected_keys={
            "*.scale_input", "*.comfy_quant", *_SIDECAR_GLOBS,
        },
        memory_cost_gb=20.4,
    ),
    # -- Wan 2.1 / 2.2 video (base t2v / i2v backbone) --------------------
    # `image_model` is "wan2.1" for BOTH 2.1 and 2.2; the 2.2 14B dual-expert
    # (high/low-noise) split is a weights+sampling difference paired at the
    # loader/generator level, not a per-checkpoint structural one. `shift`
    # defaults to ComfyUI's 8.0 (Wan-AI's own per-variant `sample_shift` runs
    # ~12 for t2v and ~3 for the 5B); `expert_boundary` is the timestep-fraction
    # switch point — Wan-AI's published Wan 2.2 `boundary` (0.875 t2v, 0.900
    # i2v), the same quantity diffusers' WanPipeline exposes as
    # `boundary_ratio`. The 5B is a single dense model — no expert boundary.
    ModelSpec(
        family="wan",
        variant="wan_t2v_14b",
        signature={"image_model": "wan2.1", "model_type": "t2v", "in_dim": 16, "out_dim": 16},
        model_class="src.platform.runtime.native.arch.wan.model:WanModel",
        sampling_settings={
            "prediction": "const",
            "shift": 8.0,
            "guidance": "cfg",
            "expert_boundary": 0.875,
        },
        latent_format={"latent_channels": 16, "format": "wan21", "temporal": True},
        clip_targets=["umt5"],
        vae_target="wan21",
        expected_unexpected_keys=set(_SIDECAR_GLOBS),
        memory_cost_gb=28.0,
    ),
    # Classic Wan 2.1 i2v: CLIP-vision conditioning via `img_emb` + i2v
    # cross-attention (detection sets model_type="i2v"). in_dim 36.
    ModelSpec(
        family="wan",
        variant="wan_i2v_14b",
        signature={"image_model": "wan2.1", "model_type": "i2v", "in_dim": 36, "out_dim": 16},
        model_class="src.platform.runtime.native.arch.wan.model:WanModel",
        sampling_settings={
            "prediction": "const",
            "shift": 8.0,
            "guidance": "cfg",
            "expert_boundary": 0.900,
        },
        latent_format={"latent_channels": 16, "format": "wan21", "temporal": True},
        clip_targets=["umt5", "clip_vision"],
        vae_target="wan21",
        expected_unexpected_keys=set(_SIDECAR_GLOBS),
        memory_cost_gb=28.0,
    ),
    # Modern Wan 2.2 i2v: reference-frame conditioning CHANNEL-CONCATENATED into
    # the DiT input (in_dim 36) with NO `img_emb` — so it uses t2v cross-attention
    # and detection reports model_type="t2v". Distinguished from the plain t2v 14B
    # purely by in_dim (36 vs 16). This is what the local Wan 2.2 i2v checkpoints
    # (Dasiwa / "Enhanced" pairs) are; the generator builds the concat.
    ModelSpec(
        family="wan",
        variant="wan22_i2v_14b",
        signature={"image_model": "wan2.1", "model_type": "t2v", "in_dim": 36, "out_dim": 16},
        model_class="src.platform.runtime.native.arch.wan.model:WanModel",
        sampling_settings={
            "prediction": "const",
            "shift": 8.0,
            "guidance": "cfg",
            "expert_boundary": 0.900,
        },
        latent_format={"latent_channels": 16, "format": "wan21", "temporal": True},
        clip_targets=["umt5"],       # concat conditioning, not CLIP-vision
        vae_target="wan21",
        expected_unexpected_keys=set(_SIDECAR_GLOBS),
        memory_cost_gb=28.0,
    ),
    ModelSpec(
        family="wan",
        variant="wan_ti2v_5b",
        signature={"image_model": "wan2.1", "model_type": "t2v", "in_dim": 48, "out_dim": 48},
        model_class="src.platform.runtime.native.arch.wan.model:WanModel",
        sampling_settings={
            "prediction": "const",
            "shift": 8.0,
            "guidance": "cfg",
        },
        latent_format={"latent_channels": 48, "format": "wan22", "spatial_downscale": 16, "temporal": True},
        clip_targets=["umt5"],
        vae_target="wan22",
        expected_unexpected_keys=set(_SIDECAR_GLOBS),
        memory_cost_gb=10.0,
    ),
    # All-in-one checkpoint note (LTX is unique — every other family ships its
    # components as separate files): a single .safetensors packs the DiT under
    # ``model.diffusion_model.*`` plus ``vae.*`` (CausalVideoAutoencoder),
    # ``audio_vae.*``, ``vocoder.*`` and ``text_embedding_projection`` at the top
    # level. The model_loader/ltx2 pipe must split ONE file by these prefixes;
    # DiT detection runs on the ``model.diffusion_model.``-stripped subset.
    ModelSpec(
        family="ltx",
        variant="ltxav",
        signature={"image_model": "ltxav"},
        model_class="src.platform.runtime.native.arch.ltx.model:LTXAVModel",
        sampling_settings={
            "prediction": "const",          # RectifiedFlowScheduler (flow matching)
            # diffusers LTX2Pipeline pins mu at max_shift=2.05 (calculate_shift is
            # called with image_seq_len == max_image_seq_len, so the linear
            # interpolation collapses to the max_shift endpoint exactly), and
            # FlowMatchEulerDiscreteScheduler's dynamic shifting applies
            # exp(mu)/(exp(mu)+(1/t-1)), which is algebraically our constant-shift
            # formula shift*t/(1+(shift-1)*t) with shift = exp(mu). ComfyUI-LTXV
            # 0.9's 2.37 was wrong for the 2.x family.
            "shift": 7.767901106306771,     # exp(2.05), diffusers LTX2Pipeline
            "guidance": "cfg",              # true CFG, no embedded guidance
        },
        latent_format={"latent_channels": 128, "format": "ltxav", "temporal": True},
        clip_targets=["gemma3_12b", "gemma4_12b"],  # 2.0/2.3 Gemma3, 2.5 Gemma4-unified (older LTXV-0.9 used t5xxl)
        vae_target="ltx_causal_video",      # + audio_vae + vocoder for the audio track
        # fp8 uses *.weight_scale; nvfp4 adds *.weight_scale_2. Both consumed by the
        # ops layer, allowlisted so neither load path trips the integrity gate.
        expected_unexpected_keys={"*.weight_scale_2", *_SIDECAR_GLOBS},
        # 2.5 checkpoints declare use_keyframes_abs_pos_embedding in their
        # embedded config but not every repack carries the weight (nvfp4 drops
        # it, int8-convrot ships a trained one — repack-dependent, not
        # model_version-dependent); upstream loads strict=False and treats the
        # parameter as absent-capability, so a missing weight is expected here
        # (post_load zero-materialises it).
        expected_missing_keys={"keyframes_abs_pos_embedding"},
        memory_cost_gb=27.0,
    ),
    ModelSpec(
        family="ltx",
        variant="ltxv",
        signature={"image_model": "ltxv"},
        model_class="src.platform.runtime.native.arch.ltx.model:LTXAVModel",
        sampling_settings={"prediction": "const", "shift": 2.37, "guidance": "cfg"},
        latent_format={"latent_channels": 128, "format": "ltxv", "temporal": True},
        clip_targets=["t5xxl"],
        vae_target="ltx_causal_video",
        expected_unexpected_keys={"*.weight_scale_2", *_SIDECAR_GLOBS},
        memory_cost_gb=20.0,
    ),
    # -- Anima (Cosmos-Predict2 MiniTrainDIT + in-model LLMAdapter) -------
    # Image DiT (t2i, in_channels 16) producing Wan21 16ch causal-3D latents.
    # ModelType.FLOW -> CONST velocity + ModelSamplingDiscreteFlow (shift 3.0,
    # multiplier 1000). Standard (true) CFG. The TE is Qwen3-0.6B, but the
    # cross-attention context is built INSIDE the DiT by the LLMAdapter fusing
    # that hidden state with T5 token ids (the generator passes t5xxl_ids /
    # t5xxl_weights through the conditioning dict). VAE is the Wan-2.1 causal-3D
    # (same as Qwen-Image / Krea-2).
    ModelSpec(
        family="anima",
        variant="anima",
        signature={"image_model": "anima"},
        model_class="src.platform.runtime.native.arch.anima.model:Anima",
        sampling_settings={
            "prediction": "const",          # flow-matching CONST velocity
            "shift": 3.0,                   # ComfyUI Anima sampling_settings
            "guidance": "cfg",              # true CFG (cond/uncond)
        },
        latent_format={"latent_channels": 16, "format": "wan21"},
        clip_targets=["qwen3_06b"],
        vae_target="qwen_image",
        expected_unexpected_keys=set(_SIDECAR_GLOBS),
        memory_cost_gb=6.0,
    ),
    # Z-Image (Alpha-VLLM NextDiT / Lumina-Image-2.0 backbone at dim 3840). One
    # spec covers turbo + base + finetunes (structurally identical); the preset
    # picks steps + cfg_scale. guidance="cfg" with cfg_scale==1.0 collapses to a
    # single forward (TrueCFG skips the uncond pass) for the distilled turbo, and
    # runs true CFG for base/finetunes.
    ModelSpec(
        family="z_image",
        variant="z_image",
        signature={"image_model": "lumina2", "z_image_modulation": True},
        model_class="src.platform.runtime.native.arch.z_image.model:ZImageDiT",
        sampling_settings={
            "prediction": "const",          # flow-matching CONST velocity
            "shift": 3.0,                   # ComfyUI ZImage sampling_settings
            "guidance": "cfg",              # true CFG; turbo drives cfg_scale=1.0
        },
        latent_format={
            # Z-Image -> Lumina2 -> ComfyUI Flux latent format (16ch, 2D flux_ae):
            # process_out(latent) = latent / scale_factor + shift_factor.
            "latent_channels": 16,
            "scale_factor": 0.3611,
            "shift_factor": 0.1159,
        },
        clip_targets=["z_image_qwen3"],     # Qwen3-4B, penultimate-layer contract
        vae_target="flux_ae",               # Flux-style 2D AE (16ch, ldm layout)
        expected_unexpected_keys=set(_SIDECAR_GLOBS),
        memory_cost_gb=13.0,
    ),
    # -- SeedVR2 (ByteDance native-resolution restoration NaDiT) -----------
    # A one-step APT (adversarial post-training) UPSCALER, not a multi-step
    # diffusion model: the generator pipe does a SINGLE DiT forward with
    # a fixed timestep and does NOT call build_sigmas / denoise_loop, so the
    # flow-matching sampling_settings below are inert for the real path. They are
    # kept minimal and valid only so a stray denoise() wouldn't crash:
    # ``guidance: "none"`` maps to NoCFG (no cond/uncond — SeedVR2 conditions on
    # fixed prompt embeddings, not classifier-free guidance). ``prediction`` is
    # not consumed by the scheduler (documentation only). The paired VAE is the
    # SeedVR2 causal-video VAE, which is SELF-NORMALIZING (folds its own 0.9152
    # scaling inside encode/decode) — hence latent_format carries only the channel
    # count and a ``"seedvr2"`` marker that NativeGenerator._is_self_normalizing_vae
    # keys off to skip the wan21 mean/std transform; no scale_factor is read here.
    # clip_targets is empty: prompt embeddings are fixed .pt tensors the model
    # loader injects (see arch/seedvr2/prompt_embedding.py), not a live TE.
    ModelSpec(
        family="seedvr2",
        variant="seedvr2_3b",
        signature={"image_model": "seedvr2", "seedvr2_variant": "3b"},
        model_class="src.platform.runtime.native.arch.seedvr2.model:SeedVR2",
        sampling_settings={
            "prediction": "const",          # inert: one-step APT bypasses the scheduler
            "guidance": "none",             # NoCFG — fixed-embedding conditioning, no CFG
        },
        latent_format={"latent_channels": 16, "format": "seedvr2"},
        clip_targets=[],                    # fixed prompt embeddings, no live text encoder
        vae_target="seedvr2",
        expected_unexpected_keys=set(_SIDECAR_GLOBS),
        memory_cost_gb=7.0,
    ),
    # SeedVR2 7B — a wider/deeper, structurally distinct NaDiT (plain-MLP blocks,
    # video-only pixel RoPE, all-multimodal, no output-norm head). Same one-step
    # APT upscale contract, same self-normalizing causal-video VAE, same fixed
    # prompt embeddings (txt width 5120). Shares the 3B's sampling/latent/vae, only
    # the arch class and memory footprint (~16.5GB fp16 DiT) differ.
    # -- MiniMax-H3 (packed-sequence audio-video DiT) ---------------------
    # fl2va and ref2va are byte-identical in schema (same 638 keys, same
    # config) -- two fine-tunes of one architecture, not two structures -- so
    # one ModelSpec covers both; the preset's per-mode model picker selects
    # which checkpoint file loads, and the generator guards family only (a
    # swapped fl2va/ref2va file cannot be caught here). Guidance-distilled: no
    # negative prompt, no CFG, one forward per step. ``shift``/``audio_shift``
    # are the released defaults for the two independent flow schedules the
    # video and audio streams step (same step count, different sigma grids,
    # inside one packed-sequence transformer call per step -- see the arch
    # module docstring). ``vae_target``/``clip_targets`` are intentionally
    # left unset: the video/audio VAEs and the Qwen3-VL-32B text encoder are
    # separate in-flight work (this spec only covers detection + the DiT).
    ModelSpec(
        family="minimax_h3",
        variant="h3",
        signature={"image_model": "minimax_h3"},
        model_class="src.platform.runtime.native.arch.minimax_h3.model:MiniMaxH3Model",
        sampling_settings={
            "prediction": "const",          # flow-matching (rectified-flow Euler)
            "shift": 12.0,                  # video stream
            "audio_shift": 3.0,             # audio stream (own, independent schedule)
            "guidance": "none",             # guidance-distilled, no CFG
        },
        latent_format={"latent_channels": 24, "format": "minimax_h3", "temporal": True},
        # fp8-scaled (pruned) checkpoints carry weight_scale/input_scale/comfy_quant
        # sidecars per quantised Linear; the ops layer consumes them at load
        # (Fp8ScaledLinear._load_from_state_dict pops them before the strict-key
        # check ever sees them), this allowlist is the same defense-in-depth every
        # other fp8-capable spec here carries.
        expected_unexpected_keys={"*.comfy_quant", *_SIDECAR_GLOBS},
        memory_cost_gb=20.0,
    ),
    # MiniMax-Music3 flow-matching DiT + fused condition encoder (text-to-music
    # only -- see the preset's `vars.music_director` contract). ``latent_channels``
    # is 128 like LTX's video latent, purely by coincidence of channel count;
    # ``resolve_preview_factors`` (sampling/preview.py) keys ch==128 to LTXV as a
    # fallback when `format` doesn't match a known string first -- Music3's 1-D
    # audio latent has no visual preview at all (there is no `format` string this
    # spec could carry that would dodge that fallback without editing that
    # module, which is out of this stage's scope; nothing wires step previews
    # for this family's own windowed euler loop, so the collision is inert
    # today -- flagged for whoever next touches sampling/preview.py).
    # ``clip_targets``/``vae_target`` are intentionally left unset: the text
    # encoder and the DAV vocoder are separate in-flight work (this spec only
    # covers detection + the DiT).
    ModelSpec(
        family="minimax_music3",
        variant="music3",
        signature={"image_model": "minimax_music3_dit"},
        model_class="src.platform.runtime.native.arch.minimax_music3.model:MiniMaxMusic3Model",
        sampling_settings={
            "prediction": "const",          # flow-matching (rectified-flow Euler)
            "steps": 30,
            "cfg": 1.7,                     # DiT classifier-free guidance
            "ar_cfg": 1.5,                  # separate CFG scale for the AR stage
            "top_k": 50,
            "guidance": "cfg",
        },
        latent_format={"latent_channels": 128, "format": "minimax_music3", "temporal": True},
        expected_unexpected_keys={"*.comfy_quant", *_SIDECAR_GLOBS},
        memory_cost_gb=5.0,                 # fp16 DiT + fused condition encoder only
    ),
    ModelSpec(
        family="seedvr2",
        variant="seedvr2_7b",
        signature={"image_model": "seedvr2", "seedvr2_variant": "7b"},
        model_class="src.platform.runtime.native.arch.seedvr2_7b.model:SeedVR27B",
        sampling_settings={
            "prediction": "const",          # inert: one-step APT bypasses the scheduler
            "guidance": "none",             # NoCFG — fixed-embedding conditioning, no CFG
        },
        latent_format={"latent_channels": 16, "format": "seedvr2"},
        clip_targets=[],                    # fixed prompt embeddings, no live text encoder
        vae_target="seedvr2",
        expected_unexpected_keys=set(_SIDECAR_GLOBS),
        memory_cost_gb=17.0,
    ),
]

for _spec in _VENDORED_SPECS:
    arch_registry.register(_spec)


def match_model_spec(config: dict[str, Any]) -> ModelSpec:
    """Return the first registered ModelSpec matching ``config``.

    Raises ``NativeEngineUnsupportedError`` (echoing the detected config) when
    nothing matches.
    """
    return arch_registry.match(config)
