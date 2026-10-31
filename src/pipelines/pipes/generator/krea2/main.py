
from __future__ import annotations

from typing import List, Optional, Tuple

from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec
from src.pipelines.outputs import ParamGenerationOutput
from src.pipelines.pipes._shared.generation.flow_generator_pipe import FlowMatchGeneratorPipe
from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes._shared.generation.guidance_options import (
    apply_schedule_settings,
    schedule_settings_config_specs,
)
from src.platform.runtime.native.sampling.flow_schedule import build_sigmas
from src.pipelines.pipes._shared.generation.config_specs import (
    device_config_spec,
    nag_config_specs,
    preview_config_spec,
    seed_config_spec,
)

# refine_tail -> how many trailing sigmas of the official 8-step grid to walk.
_REFINE_TAIL_STEPS = {"subtle": 2, "balanced": 3, "strong": 4}
_REFINE_TAIL_GRID_STEPS = 8
_REFINE_BASE_GRID_STEPS = 24
_REFINE_BASE_MIN_STEPS = 2
_REFINE_BASE_MAX_START_SIGMA = 0.8
_LATENT_TOKEN_PX = 16

# mu_schedule="dynamic" anchors (upstream krea-ai/krea-2 sampling.py
# `timesteps()`): mu = slope*seq_len + intercept through (x1, y1)/(x2, y2),
# x = (px / align) ** 2. Same anchors the ModelSpec comment in
# detect/registry.py documents for a future non-turbo variant.
_BASE_DYNAMIC_SHIFT = {"x1_px": 256, "x2_px": 1280, "y1": 0.5, "y2": 1.15, "align": 16}


class GeneratorKrea2Pipe(FlowMatchGeneratorPipe):
    name = "generator"
    description = "Native Krea-2 generator (flow matching, true CFG; turbo defaults to cfg=1)"
    family_tag = "GENERATOR KREA2"
    supports_shift = False

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        ctx = super().build_context(pipe_input)
        # Before _apply_mu_schedule, so that method's fixed_mu/dynamic_shift
        # override still lands on top for mu_schedule="dynamic".
        apply_schedule_settings(ctx, self.config)
        self._refine_tail_base_start = None
        self._refine_tail_sigmas = self._apply_refine_tail(ctx)
        self._apply_mu_schedule(ctx)
        return ctx

    def maybe_img2img(self, gen, conditioning, ctx, index, seed, progress):
        if ctx.extra.get("mode") == "img2img" and self._refine_tail_base_start is not None:
            source = self._select_source(ctx.extra.get("images") or [], index)
            if source is not None:
                width, height = self._img2img_target_size(gen, ctx, source.size)
                tail = self._base_refine_tail(self._refine_tail_base_start, width, height)
                ctx.extra["sigmas"] = tail
                self._refine_tail_sigmas = [round(float(v), 6) for v in tail.tolist()]
        return super().maybe_img2img(gen, conditioning, ctx, index, seed, progress)

    def _base_refine_tail(self, start_sigma: float, width: int, height: int):
        seq_len = max(1, (width // _LATENT_TOKEN_PX) * (height // _LATENT_TOKEN_PX))
        full = build_sigmas(
            _REFINE_BASE_GRID_STEPS,
            dynamic_shift=_BASE_DYNAMIC_SHIFT,
            image_seq_len=seq_len,
        )
        first = min(range(len(full)), key=lambda i: abs(float(full[i]) - start_sigma))
        first = min(first, _REFINE_BASE_GRID_STEPS - _REFINE_BASE_MIN_STEPS)
        tail = full[first:]
        if float(tail[0]) > _REFINE_BASE_MAX_START_SIGMA:
            raise ValueError(
                f"[{self.family_tag}] Enhance cannot keep the image intact on the Base profile at "
                f"{width}x{height}: its resolution-dependent schedule would re-noise it almost "
                f"completely. Use the Turbo profile or a smaller 'Upscale by'."
            )
        return tail

    def _apply_mu_schedule(self, ctx: GeneratorContext) -> None:
        """``mu_schedule="dynamic"``: override the ModelSpec's fixed_mu=1.15 with
        the resolution-anchored dynamic-mu interpolation via the
        ``fixed_mu``/``dynamic_shift`` whitelist (``engine._sampling_settings_for``).
        Default ``"fixed"`` leaves ``ctx.extra["schedule_settings"]`` exactly as
        ``FlowMatchGeneratorPipe.build_context`` set it (``None`` unless a preset
        already populated it) -- byte-identical to before this knob existed.
        """
        if self.config.get("mu_schedule", "fixed") != "dynamic":
            return
        ctx.extra["schedule_settings"] = {
            **(ctx.extra.get("schedule_settings") or {}),
            "fixed_mu": None,
            "dynamic_shift": _BASE_DYNAMIC_SHIFT,
        }

    def _apply_refine_tail(self, ctx: GeneratorContext) -> Optional[List[float]]:
        """Slice the official fixed-mu tail into ``ctx.extra["sigmas"]`` when
        ``refine_tail`` is set; return the sigma values for provenance (``None``
        when off). Raises loudly rather than silently no-op-ing: a
        ``refine_tail`` outside img2img mode, or on a model spec that carries
        no ``fixed_mu``, is a misconfigured preset/pipeline, not a degraded run.
        """
        refine_tail = self.config.get("refine_tail") or ""
        if not refine_tail:
            return None
        if ctx.extra.get("mode") != "img2img":
            raise ValueError(
                f"[{self.family_tag}] refine_tail={refine_tail!r} requires mode='img2img' "
                f"(got mode={ctx.extra.get('mode')!r})"
            )
        if refine_tail not in _REFINE_TAIL_STEPS:
            raise ValueError(
                f"[{self.family_tag}] unknown refine_tail {refine_tail!r}; "
                f"expected one of {sorted(_REFINE_TAIL_STEPS)}"
            )
        generator = ctx.extra["generator"]
        fixed_mu = generator.spec.sampling_settings.get("fixed_mu")
        if fixed_mu is None:
            raise ValueError(
                f"[{self.family_tag}] refine_tail requires the model spec's "
                f"sampling_settings.fixed_mu; none found for "
                f"{generator.spec.family}/{generator.spec.variant}"
            )
        tail_len = _REFINE_TAIL_STEPS[refine_tail]
        full = build_sigmas(_REFINE_TAIL_GRID_STEPS, fixed_mu=float(fixed_mu))
        tail = full[-tail_len:]
        if self.config.get("mu_schedule", "fixed") == "dynamic":
            self._refine_tail_base_start = float(tail[0])
            tail = None
        else:
            ctx.extra["guidance"] = 1.0
            ctx.extra["sampler"] = "euler"
            ctx.extra["sigmas"] = tail
        # img2img_denoise() early-returns the source unchanged when denoise<=0,
        # regardless of an explicit sigmas list -- denoise is otherwise fully
        # inert once sigmas is set (sigmas[0] fixes the noise blend), so a
        # non-positive value here would silently skip the whole refine pass.
        if ctx.extra.get("denoise", 0.0) <= 0.0:
            ctx.extra["denoise"] = 1.0
        if tail is None:
            return None
        return [round(float(v), 6) for v in tail.tolist()]

    def attach_conditioning(self, ctx: GeneratorContext, cond: dict,
                            uncond: Optional[dict]) -> Tuple[dict, Optional[dict]]:
        strength = float(self.config.get("text_enhancer_strength", 0.0))
        scale = float(self.config.get("text_scale", 1.0))
        if strength == 0.0 and scale == 1.0:
            return cond, uncond
        cond = {**cond, "text_fusion": {"strength": strength, "scale": scale}}
        if uncond:
            uncond = {**uncond, "text_fusion": {"strength": strength, "scale": scale}}
        return cond, uncond

    def emit_results(self, generation_outputs, results, used_seeds) -> None:
        super().emit_results(generation_outputs, results, used_seeds)
        sigmas = getattr(self, "_refine_tail_sigmas", None)
        if sigmas is None:
            return
        quantity = len(used_seeds)
        generation_outputs(ParamGenerationOutput(
            name="refine_tail", values=[self.config.get("refine_tail")] * quantity,
        ))
        generation_outputs(ParamGenerationOutput(
            name="refine_tail_sigmas", values=[sigmas] * quantity,
        ))

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "txt2img", "Generation mode", required=True, choices=["txt2img", "img2img", "inpaint"]),
            *cls.img2img_config_specs(default_denoise=0.55),
            PipeConfigSpec("steps", int, 8, "Denoising steps (turbo default 8)", required=False, min_value=1, max_value=100),
            PipeConfigSpec("guidance", float, 1.0, "CFG scale. 1.0 = single conditional-only forward (turbo/distilled "
                           "default, byte-identical to the old NoCFG path); above 1.0 runs a real negative-conditioned "
                           "forward pass each step -- needed for a raw/base checkpoint, optional as an experiment on "
                           "the distilled checkpoint at higher step counts.", required=False,
                           min_value=1.0, max_value=15.0),
            PipeConfigSpec("mu_schedule", str, "fixed", "Sigma-schedule mu source: 'fixed' pins the official turbo "
                           "schedule (fixed_mu=1.15); 'dynamic' switches to the resolution-anchored mu interpolation "
                           "for a raw/base (non-distilled) checkpoint (see docs/models/krea2.md).", required=False,
                           choices=["fixed", "dynamic"]),
            PipeConfigSpec("sampler", str, "euler", "Sampler: any key registered on the sampler registry "
                           "(see GET /api/sampling/catalog)", required=False),
            PipeConfigSpec("resolution", str, "1024x1024", "Resolution (WxH)", required=False),
            PipeConfigSpec("quantity", int, 1, "Number of images", required=False, min_value=1, max_value=10),
            seed_config_spec(),
            device_config_spec(),
            preview_config_spec(),
            *nag_config_specs("Normalized Attention Guidance scale (1.0 = off). Injects the negative "
                           "prompt into the joint attention so it's enforced even at guidance=1.0 (single-forward turbo "
                           "speed) — set nag_scale to ~1.1-1.5 for real negative-prompt influence without a second "
                           "forward pass. Redundant with (and unvalidated alongside) real CFG (guidance > 1.0), which "
                           "already runs a full negative-conditioned pass -- leave at 1.0 on a CFG profile."),
            PipeConfigSpec("text_enhancer_strength", float, 0.0, "Experimental text-fusion enhancer strength "
                           "(0 = off): re-runs the text fusion with deep text-encoder layers amplified and blends the "
                           "shift back in, capped per token.", required=False, min_value=0.0, max_value=2.0),
            PipeConfigSpec("text_scale", float, 1.0, "Multiplier on the fused text signal before it enters the image "
                           "blocks (1.0 = off).", required=False, min_value=0.25, max_value=4.0),
            PipeConfigSpec("refine_tail", str, "", "Whole-frame enhance (img2img only): walk only the tail of the "
                           "official fixed-mu 8-step Euler grid instead of a denoise-truncated schedule. '' = off, "
                           "'subtle'/'balanced'/'strong' = the last 2/3/4 grid steps.",
                           required=False, choices=["", "subtle", "balanced", "strong"]),
            *schedule_settings_config_specs(),
            PipeConfigSpec(
                "step_cache", dict, {},
                "FBCache step-skipping options, forwarded to NativeGenerator.sample() "
                "unmodified: {'rel_threshold': 0.12, 'warmup_steps': 4, "
                "'max_consecutive_skips': 3}. rel_threshold<=0 (default/absent) is off "
                "and never wraps the guidance strategy -- byte-identical to leaving this "
                "unset. Read directly by FlowMatchGeneratorPipe.build_context, not through "
                "the flat step_cache_threshold/warmup_steps/max_skips resolver the Wan/LTX "
                "video pipes use (this family has no such resolver).",
                required=False,
            ),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "Krea-2 model bundle", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning (per image)", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
            cls.img2img_input_spec(),
            cls.inpaint_input_spec(),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec("image", IOType.IMAGE, "Generated images", is_array=True)]
