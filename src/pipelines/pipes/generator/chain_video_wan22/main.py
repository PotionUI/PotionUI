"""Sequential (SVI Pro 2.0-style) Wan 2.2 chain-video generator.

A chain is N segments, each resolved to a per-segment SUB-TYPE
(t2v/i2v/flf/chain -- ``seg['sub_type']`` from derive_segment_routing) and
generated on the matching checkpoint SET: a fresh ``t2v`` shot on the plain
(in_dim=16) t2v experts with no conditioning; ``i2v``/``flf`` on the concat-i2v
(in_dim=36) experts conditioned on the segment's own start (and optional end)
image; ``chain`` on the i2v experts conditioned on the PREVIOUS segment's tail
frames. Both bundles arrive as separate optional inputs (``model`` = i2v set,
``model_t2v`` = t2v set); a chain that opens on a t2v shot and continues as i2v
uses both. Segments are optionally stitched into one continuous video by
dropping each non-first segment's leading overlap frames
(`stitch.stitch_segments`).

Segments may swap in a different LoRA stack per expert mid-chain -- patched onto
the live experts of whichever set the segment runs on, and un-patched back to
the set's base experts for a later plain segment. Re-acquisition goes through
the shared `acquire_wan_dit`, which reuses the ``MODELS`` lifecycle cache keyed
by DiT path + LoRA fingerprint (so the cache NEVER holds a patched-per-combo
copy; a re-used stack is a cache hit, not a reload).

Not a `BaseGeneratorPipe` seed-loop pipe: a chain's segments are NOT
independent draws of the same request (segment i's conditioning depends on
segment i-1's output), so this pipe owns its own sequential `process()`.

Registered as `generator/chain_video_wan22` (two-level pipe discovery, same
convention as `generator/img2vid_wan22` / `generator/txt2vid_wan22`).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import torch

from src.pipelines.outputs import ParamGenerationOutput, VideoGenerationOutput
from src.platform.runtime.native.engine import NativeEngineLoader
from src.platform.runtime.native.errors import SamplingNumericsError
from src.platform.runtime.native.sampling import ProgressHook, denoise, make_preview_hook
from src.platform.runtime.native.vae.causal_3d import LATENTS_MEAN, LATENTS_STD
from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    IOType,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
    PipeConfigSpec,
)
from src.pipelines.outputs import Icon
from src.pipelines.pipes._shared.generation.generator_base import emit_gallery
from src.pipelines.pipes._shared.generation.guidance_options import (
    apg_settings_config_specs,
    apg_settings_overrides,
    build_riflex,
    riflex_config_specs,
    sampler_step_cache_config_specs,
    sampler_step_cache_kwargs,
    schedule_settings_config_specs,
    schedule_settings_overrides,
    slg_settings_config_specs,
    slg_settings_overrides,
)
from src.pipelines.pipes._shared.generation.loader_helpers import (
    active_loras as _active_loras,
    path_of as _path_of,
    vram_budget as _vram_budget_fn,
)
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes._shared.media.video_encode import encode_frames_to_mp4
from src.pipelines.pipes._shared.vae.wan_tiled_encode import make_wan_vae_encode
from src.pipelines.pipes.generator.chain_video_wan22.stitch import stitch_segments
from src.pipelines.pipes.generator.img2vid_wan22.concat import build_i2v_concat
from src.pipelines.pipes.generator.img2vid_wan22.main import _prep_start_frame
from src.pipelines.pipes.generator.txt2vid_wan22.main import (
    _attach_nag,
    _decode_video,
    _ExpertRouter,
    _snap_geometry,
    _TEMPORAL_DOWNSCALE,
    _to_device,
    resolve_expert_boundary,
)
from src.pipelines.pipes.model_loader.wan22.acquire import acquire_wan_dit

_LOG_TAG = "GENERATOR WAN CHAIN"


class _ChainForward:
    """model_forward wrapping `_ExpertRouter`; prepends `concat` (the 20ch i2v
    conditioning) to the 16ch noise when set, else passes the noise through
    unchanged (t2v checkpoint / no concat). `.concat` is reassigned per
    segment rather than rebuilding the wrapper each time; the router itself
    is mutated in place (`.high`/`.low`) on a mid-chain LoRA swap.
    """

    def __init__(self, router: _ExpertRouter, concat: Optional[torch.Tensor] = None) -> None:
        self.router = router
        self.concat = concat

    def __call__(self, x: torch.Tensor, sigma: torch.Tensor, conditioning: dict) -> torch.Tensor:
        if self.concat is not None:
            x = torch.cat([x, self.concat], dim=1)
        return self.router(x, sigma, conditioning)


@dataclass
class _DecodeCtx:
    """Minimal duck-typed context `_decode_video` (txt2vid_wan22) needs."""
    vae: Any
    device: str
    latent_channels: int


def _lora_fingerprint(loras: Optional[List[Dict[str, Any]]]) -> str:
    """Same fingerprint format `acquire_wan_dit`/the model_loader use, so an
    unchanged segment-to-segment LoRA stack compares equal without an
    acquire() call (used here purely to DECIDE whether to re-acquire)."""
    active = _active_loras(loras or [])
    return "+".join(f"{l['file_path']}@{l['weight']}" for l in active) or "none"


def _compose_loras(
        base: Optional[List[Dict[str, Any]]],
        override: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Compose a segment's per-expert LoRA override ON TOP OF the set's base
    (preset-level) LoRA stack, rather than replacing it -- ``base`` is
    form-level configuration (e.g. a Fast profile's Lightning speed pair), not
    something a segment override should be able to silently drop. An override
    entry sharing a ``file_path`` with a base entry WINS (its weight replaces
    the base one's); every other base entry survives unchanged, followed by
    the override's own entries. An empty (but non-None) override list keeps
    the base stack verbatim."""
    override_active = _active_loras(override or [])
    override_paths = {l["file_path"] for l in override_active}
    base_active = _active_loras(base or [])
    kept_base = [l for l in base_active if l["file_path"] not in override_paths]
    return kept_base + override_active


def _tail_to_start_frames(tail: np.ndarray, device, dtype: torch.dtype) -> torch.Tensor:
    """Convert a `(n, H, W, 3)` uint8 [0,255] tail (as `_decode_video` returns)
    into the `(n, H, W, 3)` float [0,1] tensor `build_i2v_concat` expects as
    `start_frames` -- the same convention `_prep_start_frame` produces for a
    fresh upload."""
    t = torch.from_numpy(np.ascontiguousarray(tail)).to(device=device, dtype=dtype)
    return t / 255.0


_I2V_SUB_TYPES = frozenset({"i2v", "flf", "chain"})


def segment_sub_type(seg: Dict[str, Any], index: int, has_first: bool, has_last: bool) -> str:
    """Resolved sub-type for a chain segment. The canonical document already
    carries ``seg['sub_type']`` (normalize_video_director ->
    derive_segment_routing); this only re-derives as a defensive fallback for a
    hand-built document, MIRRORING that backend contract (a t2v opener, i2v when
    a start image is present, flf with a start+end pair, chain for a prompt-only
    continuation)."""
    st = seg.get("sub_type")
    if st in ("t2v", "i2v", "flf", "chain"):
        return st
    if has_first and has_last:
        return "flf"
    if has_first:
        return "i2v"
    return "t2v" if index == 0 else "chain"


@dataclass
class _ModelSet:
    """Mutable per-set state carried across the segment loop: the loaded bundle,
    the re-acquire paths, a lazily-built expert router, and the currently-applied
    per-expert LoRA fingerprints (so an unchanged stack is not re-acquired)."""
    bundle: Any
    high_path: Optional[str]
    low_path: Optional[str]
    router: Optional[_ExpertRouter] = None
    sampling_settings: Optional[dict] = None
    high_fp: Optional[str] = None
    low_fp: Optional[str] = None
    # Snapshots taken in prepare_set. bundle.spec / bundle.high_dit deref the
    # bundle's WEAK refs, which the lifecycle cache can null mid-chain (a
    # per-segment LoRA re-acquire may evict the base under RAM pressure) --
    # per-segment reads must use these snapshots, never the bundle.
    spec: Any = None
    native_dtype: Any = None
    patch_size: Optional[int] = None
    # STRONG NativeModel handles resolved once in prepare_set. Holding them for
    # the run's duration makes the lifecycle manager's outside-refs eviction
    # guard protect them mid-chain; the bundle's own weak refs stay weak for
    # everything that outlives the run.
    base_high: Any = None
    base_low: Any = None
    vae: Any = None
    # The set's preset-level base LoRA stacks (from the bundle the model_loader
    # pipe built), snapshotted alongside base_high/base_low in prepare_set for
    # the same reason -- a segment's per-expert override composes on top of
    # these, it never replaces them (see _compose_loras).
    base_loras_high: List[Dict[str, Any]] = field(default_factory=list)
    base_loras_low: List[Dict[str, Any]] = field(default_factory=list)


class GeneratorWanChainVideoPipe(BasePipe):
    name = "generator"
    description = "Native Wan 2.2 sequential chain-video generator (SVI Pro 2.0-style continuation)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "mode": "chain",
            "document": None,
            "motion_latent_count": 1,
            "anchor_latent_strength": 1.0,
            "seam_handoff": "latent",
            "steps": 30,
            "cfg": 5.0,
            "sampler": "unipc",
            "resolution": "832x480",
            "fps": 16.0,
            "expert_boundary": None,
            "expert_switch_step": None,
            "nag_scale": 1.0,
            "nag_tau": 3.5,
            "nag_alpha": 0.5,
            "dtype": "bfloat16",
            "t2v_high_noise_model": None,
            "t2v_low_noise_model": None,
            "i2v_high_noise_model": None,
            "i2v_low_noise_model": None,
            "device": "cuda",
            "preview": True,
            "cfg_zero_star": True,
            "zero_init_steps": 0,
            "riflex": False,
            "riflex_trained_frames": None,
            # None-sentinel: guidance_options.py's *_overrides() only emit a key
            # when non-None, so an unset knob here lets the model's own
            # ModelSpec.sampling_settings survive the merge (see that module's
            # docstring; P5 fix).
            "apg_eta": None,
            "apg_norm_threshold": None,
            "apg_momentum": None,
            "slg_scale": None,
            "slg_layers": None,
            "slg_sigma_start": None,
            "slg_sigma_end": None,
            "sampler_options": {},
            "step_cache": {},
            "schedule": "",
            "schedule_options": {},
            "manual_sigmas": "",
            "detail_strength": None,
            "detail_start": None,
            "detail_end": None,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "chain", "Generation mode", required=True, choices=["chain"]),
            PipeConfigSpec("document", dict, None,
                           "Canonical Video Director document (schema_version 1, mode='chain'); "
                           "segments[] drives the sequential loop", required=True),
            PipeConfigSpec("motion_latent_count", int, 1,
                           "SVI Pro: how many temporal LATENT slots of the previous segment's tail "
                           "carry into the next segment's conditioning (1 slot ~= 4 pixel frames). "
                           "Lower keeps a lighter motion hand-off; raise to re-inject the full tail",
                           required=False, min_value=1, max_value=4),
            PipeConfigSpec("anchor_latent_strength", float, 1.0,
                           "SVI Pro: mask weight on each segment's anchor (start) frames. 1.0 hard-locks "
                           "them (identity); lower loosens the lock so dynamic scenes can move off the anchor",
                           required=False, min_value=0.0, max_value=1.0),
            PipeConfigSpec("seam_handoff", str, "latent",
                           "Chain continuation seam conditioning. 'latent' (default) splices the previous "
                           "segment's own SAMPLED latent tail directly into the next segment's locked slots, "
                           "bypassing the decode->uint8->re-encode round trip (removes two of the "
                           "three seam color-shift sources). 'pixel' reproduces the original round-tripped "
                           "hand-off exactly, for A/B comparison against the previous default.",
                           required=False, choices=["latent", "pixel"]),
            PipeConfigSpec("steps", int, 30, "Default denoising steps (segment override: segments[i].steps)",
                           required=False, min_value=1, max_value=150),
            PipeConfigSpec("cfg", float, 5.0, "Default true CFG scale (segment override: segments[i].cfg)",
                           required=False, min_value=0.0, max_value=30.0),
            PipeConfigSpec("sampler", str, "unipc", "Sampler", required=False,
                           choices=["euler", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_3m", "res_multistep", "unipc", "lcm"]),
            PipeConfigSpec("resolution", str, "832x480", "Resolution (WxH), constant across all segments", required=False),
            PipeConfigSpec("fps", float, 16.0, "Output frame rate", required=False, min_value=1.0, max_value=60.0),
            PipeConfigSpec("expert_boundary", float, None, "Dual-expert switch boundary override (sigma fraction)",
                           required=False, min_value=0.0, max_value=1.0),
            PipeConfigSpec("expert_switch_step", int, None, "Switch high->low expert at this step (wins over expert_boundary; converted to that step's sigma)",
                           required=False, min_value=0),
            PipeConfigSpec("dtype", str, "bfloat16",
                           "Compute dtype (must match model_loader/wan22's dtype for cache reuse on re-acquire)",
                           required=False, choices=["bfloat16", "float16", "float32"]),
            PipeConfigSpec("t2v_high_noise_model", dict, None,
                           "t2v high-noise (or only) DiT file -- re-acquire target for a t2v segment's LoRA swap", required=False),
            PipeConfigSpec("t2v_low_noise_model", dict, None,
                           "t2v low-noise expert DiT file (Wan 2.2 dual-expert only)", required=False),
            PipeConfigSpec("i2v_high_noise_model", dict, None,
                           "i2v high-noise (or only) DiT file -- re-acquire target for an i2v/chain segment's LoRA swap", required=False),
            PipeConfigSpec("i2v_low_noise_model", dict, None,
                           "i2v low-noise expert DiT file (Wan 2.2 dual-expert only)", required=False),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            PipeConfigSpec("preview", bool, True, "Emit live latent previews to the workbench during sampling", required=False),
            PipeConfigSpec("cfg_zero_star", bool, True,
                           "CFG-Zero*: rescale the uncond branch onto cond before extrapolation", required=False),
            PipeConfigSpec("zero_init_steps", int, 0, "CFG-Zero*: zero velocity for the first N steps",
                           required=False, min_value=0, max_value=100),
            *riflex_config_specs(),
            *apg_settings_config_specs(),
            *slg_settings_config_specs(),
            *sampler_step_cache_config_specs(),
            *schedule_settings_config_specs(),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            # Both bundles are OPTIONAL: a chain may resolve to only one set. The
            # process() loop raises a clear error if a segment needs the set that
            # wasn't loaded.
            PipeInputSpec("model", IOType.MODEL, False,
                          "i2v Wan bundle (base LoRA stacks applied); runs i2v/flf/chain segments", is_array=False),
            PipeInputSpec("model_t2v", IOType.MODEL, False,
                          "t2v Wan bundle (base LoRA stacks applied); runs t2v segments", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True,
                          "Encoded prompt conditioning, one per chain segment (index-aligned)", is_array=True),
            PipeInputSpec("image", IOType.IMAGE, False,
                          "Start image(s), one per i2v/flf segment (document-order, role=first)", is_array=True),
            PipeInputSpec("end_image", IOType.IMAGE, False,
                          "End image(s), one per flf segment (document-order, role=last)", is_array=True),
            PipeInputSpec("MODELS", IOType.SERVICE, False,
                          "Model lifecycle service, for per-segment LoRA re-acquisition", is_array=False),
            PipeInputSpec("GPU", IOType.SERVICE, False,
                          "GPU manager for the VRAM budget (re-acquisition loader)", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("video", IOType.VIDEO,
                           "Generated videos (one per segment, plus the stitched result when enabled)", is_array=True),
        ]

    # -- process -------------------------------------------------------

    def process(
            self,
            pipe_input: PipeInput,
            generation_outputs: callable,
            is_cancelled: Optional[callable] = None,
    ) -> PipeOutput:
        conditioning = pipe_input.input.get("conditioning") or []
        images = pipe_input.input.get("image") or []
        end_images = pipe_input.input.get("end_image") or []
        models = pipe_input.input.get("MODELS", None)

        document = self.config.get("document") or {}
        segments = document.get("segments") or []
        if not segments:
            raise ValueError("generator/chain_video_wan22 requires document.segments (at least one)")

        settings = document.get("settings") or {}
        continuation = settings.get("continuation")
        default_overlap = 4 if continuation is None else int(continuation.get("overlap_frames", 4))
        continuation_source = None if continuation is None else continuation.get("source")
        stitch_enabled = True if continuation is None else bool(continuation.get("stitch", True))
        if continuation_source == "last_frame":
            default_overlap = 1

        # Map each segment id to its start (role=first) / end (role=last) image
        # in the loaded arrays -- media_loader loads them in document order, so
        # the Nth role=first entry is images[N].
        doc_media = document.get("media") or []
        first_index: Dict[Any, int] = {}
        last_index: Dict[Any, int] = {}
        for m in doc_media:
            seg_id, role = m.get("segment_id"), m.get("role")
            if role == "first" and seg_id not in first_index:
                first_index[seg_id] = len(first_index)
            elif role == "last" and seg_id not in last_index:
                last_index[seg_id] = len(last_index)

        # The two checkpoint sets. Either may be absent (a chain may resolve to
        # only one set); a segment needing a missing set raises below.
        sets: Dict[str, _ModelSet] = {
            "t2v": _ModelSet(
                bundle=pipe_input.input.get("model_t2v"),
                high_path=_path_of(self.config.get("t2v_high_noise_model")),
                low_path=_path_of(self.config.get("t2v_low_noise_model")),
            ),
            "i2v": _ModelSet(
                bundle=pipe_input.input.get("model"),
                high_path=_path_of(self.config.get("i2v_high_noise_model")),
                low_path=_path_of(self.config.get("i2v_low_noise_model")),
            ),
        }

        steps = int(self.config.get("steps", 30))
        cfg = float(self.config.get("cfg", 5.0))
        sampler = self.config.get("sampler", "unipc")
        fps = float(self.config.get("fps", 16.0))
        device = self.config.get("device", "cuda")
        dtype_name = self.config.get("dtype", "bfloat16")
        resolution = str(self.config.get("resolution", "832x480")).split("x")
        width, height = int(resolution[0]), int(resolution[1])
        motion_latent_count = int(self.config.get("motion_latent_count", 1))
        anchor_latent_strength = float(self.config.get("anchor_latent_strength", 1.0))
        seam_handoff = str(self.config.get("seam_handoff", "latent"))
        # Rolling hand-off: lock this many pixel frames of the previous segment's
        # tail at the front of the next segment's window, bounded by both the
        # configured overlap and motion_latent_count's latent-slot budget.
        base_tail = default_overlap if default_overlap > 0 else 1
        motion_frames = (motion_latent_count - 1) * _TEMPORAL_DOWNSCALE + 1
        tail_count = max(1, min(base_tail, motion_frames))
        # Same tail, in LATENT frames -- the same (n-1)//4+1 conversion used for
        # any pixel-frame count in this pipe (see `t_lat` below). seam_handoff
        # "latent" splices exactly this many of the previous segment's own
        # SAMPLED latent frames; "pixel" never reads it.
        tail_latent_slots = (tail_count - 1) // _TEMPORAL_DOWNSCALE + 1
        riflex = build_riflex(self.config)

        def prepare_set(mset: _ModelSet, set_name: str) -> _ModelSet:
            """Lazily validate a set's checkpoint arch and build its expert
            router (base experts, no per-segment LoRA yet). Idempotent."""
            if mset.bundle is None:
                raise ValueError(
                    f"generator/chain_video_wan22: a segment needs the {set_name} checkpoint set, but no "
                    f"{set_name} model was loaded. Fill the {set_name}_high/{set_name}_low pickers, or remove "
                    f"the segment(s) requiring it."
                )
            if mset.router is not None:
                return mset
            spec = mset.bundle.spec
            mset.spec = spec
            mset.base_high = mset.bundle.high_dit
            mset.base_low = mset.bundle.low_dit
            mset.vae = mset.bundle.vae
            mset.base_loras_high = list(getattr(mset.bundle, "loras_high", None) or [])
            mset.base_loras_low = list(getattr(mset.bundle, "loras_low", None) or [])
            mset.native_dtype = mset.base_high.compute_dtype
            mset.patch_size = mset.base_high.module.patch_size
            in_dim = mset.base_high.module.in_dim
            if set_name == "t2v" and in_dim != 16:
                raise ValueError(
                    f"generator/chain_video_wan22: the t2v set holds '{spec.variant}' (in_dim={in_dim}); a t2v "
                    f"shot needs a plain t2v Wan checkpoint (in_dim=16)."
                )
            if set_name == "i2v" and in_dim != 36:
                raise ValueError(
                    f"generator/chain_video_wan22: the i2v set holds '{spec.variant}' (in_dim={in_dim}); an "
                    f"image-conditioned/chain shot needs a concat-i2v Wan checkpoint (in_dim=36)."
                )
            mset.sampling_settings = {
                **spec.sampling_settings,
                **apg_settings_overrides(self.config),
                **slg_settings_overrides(self.config),
                **schedule_settings_overrides(self.config),
            }
            # Provisional boundary only -- an 'expert_switch_step' converts to a
            # sigma against a SPECIFIC step count's schedule, and a chain segment
            # may override 'steps' away from the pipe-level default (e.g. a
            # distilled 4-step LoRA segment while the document default is 30).
            # rebind_boundary() below recomputes this per segment from that
            # segment's OWN step count before it runs, so this call only fixes a
            # value for router construction; it is never relied on for a real
            # denoise.
            boundary = resolve_expert_boundary(spec, self.config, mset.sampling_settings, steps, log_tag=_LOG_TAG)
            mset.router = _ExpertRouter(mset.base_high, mset.base_low, boundary, device, riflex=riflex)
            mset.high_fp = mset.low_fp = "base"
            return mset

        def rebind_boundary(mset: _ModelSet, steps_i: int) -> None:
            """Recompute the set's expert-switch boundary against THIS segment's
            own step count and rebind it onto the (already-built, reused) router.
            Without this, a segment whose 'steps' override differs from the
            pipe-level default gets a boundary sigma converted from the WRONG
            schedule length, so the router can switch experts far outside the
            noise regime either expert was trained for -- a real source of
            numerical blowup with a distilled few-step LoRA, not just a quality
            regression (see resolve_expert_boundary's step->sigma conversion)."""
            mset.router.boundary = resolve_expert_boundary(
                mset.spec, self.config, mset.sampling_settings, steps_i, log_tag=_LOG_TAG,
            )

        def apply_segment_loras(mset: _ModelSet, set_name: str, seg_loras: Optional[Dict[str, Any]]) -> None:
            """Patch/unpatch the live experts for a segment's LoRA stack. No
            override (seg_loras is None) restores the set's BASE experts, so a
            LoRA'd segment never leaks into a later plain one. An override
            COMPOSES onto the set's base LoRA stack (_compose_loras) instead of
            replacing it -- the base stack is preset-level configuration (e.g.
            a Fast profile's Lightning speed pair), not a per-segment concern,
            so it must survive every segment including an overridden one. The
            lifecycle cache keys the BASE checkpoint by path + LoRA fingerprint
            (of the COMPOSED stack), so a re-used stack is a cache hit, never a
            fresh patched copy."""
            if seg_loras is not None and not mset.high_path:
                raise ValueError(
                    f"generator/chain_video_wan22: a segment uses per-segment LoRA overrides, but config has no "
                    f"{set_name}_high_noise_model file_path to re-acquire from."
                )
            router = mset.router
            composed_high = None if seg_loras is None else _compose_loras(mset.base_loras_high, seg_loras.get("high"))
            want_high = "base" if seg_loras is None else _lora_fingerprint(composed_high)
            if want_high != mset.high_fp:
                router.high = (
                    mset.base_high if seg_loras is None
                    else acquire_wan_dit(models, loader, mset.high_path, dtype_name, composed_high, log_tag=_LOG_TAG)
                )
                mset.high_fp = want_high
            if mset.base_low is not None and mset.low_path:
                composed_low = None if seg_loras is None else _compose_loras(mset.base_loras_low, seg_loras.get("low"))
                want_low = "base" if seg_loras is None else _lora_fingerprint(composed_low)
                if want_low != mset.low_fp:
                    router.low = (
                        mset.base_low if seg_loras is None
                        else acquire_wan_dit(models, loader, mset.low_path, dtype_name, composed_low, log_tag=_LOG_TAG)
                    )
                    mset.low_fp = want_low

        vram_gb = _vram_budget_fn(pipe_input, None, _LOG_TAG)
        loader = NativeEngineLoader(device=device, vram_gb=vram_gb)

        base_seed = settings.get("seed")
        base_seed = -1 if base_seed is None else int(base_seed)
        if base_seed == -1:
            base_seed = int(torch.randint(0, 2 ** 31 - 1, (1,)).item())

        seg_steps_list = [
            int(seg.get("steps")) if seg.get("steps") is not None else steps for seg in segments
        ]
        total_steps = sum(seg_steps_list) or 1

        segment_paths: List[str] = []
        segment_seeds: List[int] = []
        prev_tail: Optional[np.ndarray] = None
        prev_latent_tail: Optional[torch.Tensor] = None
        chain_trimmed = False
        progress = ProgressEmitter(generation_outputs, title=self.name)
        n = len(segments)
        cancelled = False
        i, sub_type, set_name = -1, None, None

        try:
            for i, seg in enumerate(segments):
                if is_cancelled and is_cancelled():
                    cancelled = True
                    break

                seg_id = seg.get("id")
                has_first = seg_id in first_index
                has_last = seg_id in last_index
                sub_type = segment_sub_type(seg, i, has_first, has_last)
                set_name = "i2v" if sub_type in _I2V_SUB_TYPES else "t2v"
                mset = prepare_set(sets[set_name], set_name)
                bundle = mset.bundle
                native_dtype = mset.native_dtype
                lf = mset.spec.latent_format

                if sub_type == "chain" and prev_tail is None:
                    raise ValueError(
                        f"generator/chain_video_wan22: segment {i} ('{seg_id}') resolves to a 'chain' "
                        f"continuation but there is no previous segment to continue from."
                    )

                apply_segment_loras(mset, set_name, seg.get("loras"))

                seed_i = seg.get("seed")
                seed_i = int(seed_i) if seed_i is not None else base_seed + i

                steps_i = seg_steps_list[i]
                cfg_i = seg.get("cfg")
                cfg_i = float(cfg_i) if cfg_i is not None else cfg
                rebind_boundary(mset, steps_i)

                frames_i = seg.get("frames")
                if frames_i is None:
                    raise ValueError(f"generator/chain_video_wan22: segments[{i}] has no 'frames'")
                seg_width, seg_height, frames_i = _snap_geometry(
                    bundle, lf, width, height, int(frames_i), patch_size=mset.patch_size)

                # Build the segment's conditioning concat: nothing for a fresh
                # t2v shot; the start image for i2v; start+end for flf; the
                # previous segment's tail for a chain continuation.
                concat = None
                tail_latent = None
                if sub_type != "t2v":
                    if sub_type == "chain":
                        start = _tail_to_start_frames(prev_tail, device, native_dtype)
                        end = None
                        if seam_handoff == "latent" and prev_latent_tail is not None:
                            tail_latent = prev_latent_tail.to(device=device, dtype=native_dtype)
                    else:
                        if not has_first:
                            raise ValueError(
                                f"generator/chain_video_wan22: segment {i} ('{seg_id}') is '{sub_type}' but has "
                                f"no start image."
                            )
                        start = _prep_start_frame(images[first_index[seg_id]], seg_height, seg_width, device, native_dtype)
                        end = (_prep_start_frame(end_images[last_index[seg_id]], seg_height, seg_width, device, native_dtype)
                               if sub_type == "flf" and has_last else None)
                    mset.vae.move_to(device)
                    with torch.no_grad():
                        concat = build_i2v_concat(
                            start, make_wan_vae_encode(mset.vae.module, seg_width, seg_height, log_prefix=_LOG_TAG),
                            length=frames_i, height=seg_height, width=seg_width,
                            latents_mean=LATENTS_MEAN, latents_std=LATENTS_STD, end_frames=end,
                            anchor_frames=None, anchor_strength=anchor_latent_strength,
                            tail_latent=tail_latent,
                            device=device, dtype=native_dtype,
                        ).to(native_dtype)
                    mset.vae.offload()
                forward = _ChainForward(mset.router, concat)

                cond_model = conditioning[i] if i < len(conditioning) else conditioning[-1]
                cond = _to_device(cond_model.embeds, device, native_dtype)
                uncond = _to_device(cond_model.n_embeds, device, native_dtype) if cond_model.n_embeds else None
                cond = _attach_nag(cond, uncond, self.config)

                t_lat = (frames_i - 1) // _TEMPORAL_DOWNSCALE + 1
                spatial_downscale = lf.get("spatial_downscale", 8)
                shape = (1, lf["latent_channels"], t_lat, seg_height // spatial_downscale, seg_width // spatial_downscale)
                gen = torch.Generator(device=device).manual_seed(seed_i)
                noise = torch.randn(shape, generator=gen, device=device, dtype=native_dtype)
                latents = torch.zeros(shape, device=device, dtype=native_dtype)
                mset.router.latents_shape = shape

                steps_before = sum(seg_steps_list[:i])

                def on_progress(_frac, step_index, _total, _i=i, _before=steps_before):
                    progress.step(_before + step_index + 1, total_steps, state=f"CHAIN {_i + 1}/{n}",
                                  icon=Icon(name="film", effect="pulse"))

                logger.debug("[%s] segment %d/%d (%s, %s set), seed %d, frames %d, latent %s",
                            _LOG_TAG, i + 1, n, sub_type, set_name, seed_i, frames_i, shape)
                hooks = [ProgressHook(on_progress)]
                if self.config.get("preview", True):
                    preview_hook = make_preview_hook(mset.spec, progress.preview)
                    if preview_hook is not None:
                        hooks.append(preview_hook)

                latent = denoise(
                    forward, latents, cond, uncond,
                    steps=steps_i, sampler_name=sampler,
                    sampling_settings=mset.sampling_settings, guidance_scale=cfg_i,
                    seed_noise=noise, hooks=hooks, is_cancelled=is_cancelled,
                    cfg_zero_star=bool(self.config.get("cfg_zero_star", True)),
                    zero_init_steps=int(self.config.get("zero_init_steps", 0)),
                    expert_boundary=mset.router.boundary if mset.router.low is not None else None,
                    # Seed determinism (stochastic samplers): reuse this
                    # segment's own `gen` -- see ensure_sampler_generator's
                    # docstring. Per-segment (not per-chain), matching the
                    # per-segment seed each segment already draws its own
                    # noise from.
                    **sampler_step_cache_kwargs(self.config, sampler=sampler, generator=gen),
                )
                if mset.router.active is not None:
                    mset.router.active.offload()

                # Captured BEFORE decode -- this segment's own sampled latent tail,
                # for the NEXT segment's seam splice (seam_handoff="latent"). Cloned
                # so the slice owns its storage instead of keeping the full-clip
                # latent alive.
                if seam_handoff == "latent":
                    n_avail = min(tail_latent_slots, latent.shape[2])
                    prev_latent_tail = latent[:, :, -n_avail:].detach().clone()

                decode_ctx = _DecodeCtx(vae=mset.vae, device=device, latent_channels=lf["latent_channels"])
                frames_px = _decode_video(decode_ctx, latent)

                # A chain segment's front is locked to the previous segment's tail
                # (rolling hand-off), not the chain's original frame; drop it from
                # the emitted clip so the part starts on real new content.
                if sub_type == "chain":
                    context_prefix = tail_count
                    if frames_px.shape[0] > context_prefix + 1:
                        frames_px = frames_px[context_prefix:]
                        chain_trimmed = True

                # Keep a tail after every segment so the NEXT segment can continue
                # from it if it resolves to 'chain'.
                prev_tail = frames_px[-tail_count:]

                out_path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
                encode_frames_to_mp4(frames_px, out_path, fps=fps)
                generation_outputs(VideoGenerationOutput(video_path=out_path, temporary=True, seed=seed_i))

                segment_paths.append(out_path)
                segment_seeds.append(seed_i)
        except Exception as exc:
            if isinstance(exc, SamplingNumericsError):
                # A watchdog trip several steps into segment i's OWN sampling
                # loop looks identical to one several steps into i-1's tail
                # from outside this pipe -- attribute it to the segment that
                # actually failed (the generic sampling loop has no way to
                # know this itself).
                exc.annotate_segment(i, f"{sub_type}/{set_name}" if sub_type else None)
            self._release_gpu(sets)
            raise

        final_paths = list(segment_paths)
        if not cancelled and stitch_enabled and len(segment_paths) > 1:
            # Trimmed continuations no longer reproduce the previous tail, so they
            # abut with no overlap to drop; only the un-trimmed path keeps one.
            stitch_overlap = 0 if chain_trimmed else default_overlap
            stitched_path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
            stitch_segments(segment_paths, stitch_overlap, stitched_path, fps)
            final_paths.append(stitched_path)

        # Every segment (and the stitched result) is generated
        # at the same configured (width, height) -- no per-segment resize --
        # so a single resolution applies to all of `final_paths`.
        emit_gallery(generation_outputs, images=[], seeds=None, videos=final_paths, video_resolution=(width, height))
        generation_outputs(ParamGenerationOutput(name="seed", values=[base_seed]))
        generation_outputs(ParamGenerationOutput(name="segment_seed", values=segment_seeds))

        return PipeOutput(output={"video": final_paths})

    @staticmethod
    def _release_gpu(sets: Dict[str, _ModelSet]) -> None:
        """Best-effort GPU cleanup on a failed segment: offload whichever
        expert(s)/VAE of either set may be resident. Never raises -- cleanup
        that raised would mask the original generation failure (mirrors
        `BaseGeneratorPipe._release_gpu_on_error`)."""
        for mset in sets.values():
            if mset.bundle is None:
                continue
            router = mset.router
            for dit in ((router.high, router.low) if router is not None else ()):
                if dit is None:
                    continue
                try:
                    dit.offload()
                except Exception:
                    pass
            try:
                (mset.vae if mset.vae is not None else mset.bundle.vae).offload()
            except Exception:
                pass
