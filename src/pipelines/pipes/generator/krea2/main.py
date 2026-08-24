"""Generator for native Krea-2 (txt2img).

Mirrors ``generator/flux``: consumes the ``model`` bundle from
``model_loader/krea2`` and the ``conditioning`` from the shared ``prompt_encoder``
pipe, runs ``NativeGenerator.sample`` per seed, decodes and emits through the
shared gallery path. Krea-2 turbo defaults: 8 steps, cfg 1.0 (true CFG collapses
to a single forward at scale 1.0 -- see the ModelSpec's ``guidance: "cfg"`` in
``detect/registry.py``), the official fixed-mu=1.15 schedule. The Krea-2 DiT is
driven through its flat ``forward`` adapter, so ``NativeGenerator.sample`` needs
no Krea-specific wiring.

``mu_schedule`` (BE-CFG-KREA2) switches the sigma-schedule mu SOURCE: ``"fixed"``
(default) is the ModelSpec's own fixed_mu=1.15 (the distilled turbo checkpoint's
official schedule); ``"dynamic"`` overrides it with the resolution-anchored
mu interpolation upstream documents for the un-distilled base/midtrain
checkpoint (see the ModelSpec comment). One architecture signature covers both
checkpoints, so the *preset* -- not detection -- picks the regime; this pipe
only translates that choice into the ``schedule_settings`` override
``NativeGenerator.sample`` whitelists (``engine._sampling_settings_for``).

``build_context``/``generate_one`` (shared by every native flow-matching family)
live in ``FlowMatchGeneratorPipe``; this module only carries Krea-2's own config
schema/defaults, and opts out of the shift override (``supports_shift = False``)
since Krea-2 is a turbo model with no shift knob.

``nag_scale``/``nag_tau``/``nag_alpha`` wire Normalized Attention
Guidance (arXiv:2505.21179) into Krea-2's joint attention (see
``arch/krea2/model.py``/``layers.py``) -- makes the negative prompt actually
count at guidance=0 (NoCFG turbo). Attached to the cond dict by
``FlowMatchGeneratorPipe.generate_one``'s ``_attach_nag``, inert at the
default ``nag_scale=1.0``.

``refine_tail`` is the "whole-frame enhance" mode: img2img-only, it
slices the tail of Krea-2's official fixed-mu 8-step Euler grid
(``build_sigmas(8, fixed_mu=<spec>)``) and hands it to the shared img2img path
as an explicit ``sigmas`` schedule (see ``Img2ImgGeneratorMixin.maybe_img2img``,
which forwards ``ctx.extra["sigmas"]`` unchanged). ``fixed_mu`` is read off the
loaded model's own ``ModelSpec.sampling_settings`` rather than hardcoded here,
so a future Krea-2 variant with a different (or absent) fixed-mu schedule is
never silently misrendered.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec, logger
from src.pipelines.outputs import ParamGenerationOutput
from src.pipelines.pipes._shared.generation.flow_generator_pipe import FlowMatchGeneratorPipe
from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.platform.runtime.native.sampling.flow_schedule import build_sigmas

# refine_tail -> how many trailing sigmas of the official 8-step grid to walk.
_REFINE_TAIL_STEPS = {"subtle": 2, "balanced": 3, "strong": 4}
_REFINE_TAIL_GRID_STEPS = 8

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
        self._release_idle_te(pipe_input)
        self._refine_tail_sigmas = self._apply_refine_tail(ctx)
        self._apply_mu_schedule(ctx)
        return ctx

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
        ctx.extra["sigmas"] = tail
        # img2img_denoise() early-returns the source unchanged when denoise<=0,
        # regardless of an explicit sigmas list -- denoise is otherwise fully
        # inert once sigmas is set (sigmas[0] fixes the noise blend), so a
        # non-positive value here would silently skip the whole refine pass.
        if ctx.extra.get("denoise", 0.0) <= 0.0:
            ctx.extra["denoise"] = 1.0
        return [round(float(v), 6) for v in tail.tolist()]

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

    def _release_idle_te(self, pipe_input: PipeInput) -> None:
        """Evict the TE's MODELS cache entry before sampling: by generator time
        ``prompt_encoder`` has already produced the conditioning every mode
        needs, so the multi-GB Qwen3-VL TE is dead weight through sampling and
        decode regardless of mode. Mirrors ``generator/qwen``'s ``_release_idle_te``
        and ``latent_upscaler/ltx``'s ``_unload_idle_te`` -- same
        ``bundle.te_cache_key`` + ``models.evict_dead_weight`` mechanism.

        Best-effort and silent: a missing ``te_cache_key`` (bundle built outside
        the MODELS cache), a missing ``MODELS`` service, or an eviction that
        raises are all "nothing to do" -- a VRAM optimisation must never fail a
        generation. Inherited by the krea2-edit plugin's generator, whose
        ref-doubled edit sequence is exactly the OOM this releases room for.
        """
        bundle = pipe_input.input.get("model")
        models = pipe_input.input.get("MODELS")
        key = getattr(bundle, "te_cache_key", None)
        if not key or models is None:
            return
        evict = getattr(models, "evict_dead_weight", None)
        if not callable(evict):
            return
        try:
            evict(key)
        except Exception:  # pragma: no cover - best-effort; never fail the pipe over this
            logger.debug("[%s] TE eviction failed for key=%r", self.family_tag, key, exc_info=True)

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "mode": "txt2img",
            "steps": 8,             # turbo distilled
            "guidance": 1.0,        # true CFG, but scale 1.0 = single forward (turbo default)
            "mu_schedule": "fixed", # ModelSpec's own fixed_mu=1.15 (turbo/distilled schedule)
            "sampler": "euler",
            "resolution": "1024x1024",
            "quantity": 1,
            "seed": -1,
            "device": "cuda",
            "denoise": 0.55,
            "img2img_scale": 0.0,
            "preview": True,
            "nag_scale": 1.0,
            "nag_tau": 3.5,
            "nag_alpha": 0.5,
            "refine_tail": "",
            "step_cache": {},
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "txt2img", "Generation mode", required=True, choices=["txt2img", "img2img"]),
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
            PipeConfigSpec("sampler", str, "euler", "Sampler", required=False, choices=["euler", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_3m", "res_multistep", "unipc", "lcm"]),
            PipeConfigSpec("resolution", str, "1024x1024", "Resolution (WxH)", required=False),
            PipeConfigSpec("quantity", int, 1, "Number of images", required=False, min_value=1, max_value=10),
            PipeConfigSpec("seed", int, -1, "Random seed", required=False, min_value=-1),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            PipeConfigSpec("preview", bool, True, "Emit live latent previews to the workbench during sampling", required=False),
            PipeConfigSpec("nag_scale", float, 1.0, "Normalized Attention Guidance scale (1.0 = off). Injects the negative "
                           "prompt into the joint attention so it's enforced even at guidance=1.0 (single-forward turbo "
                           "speed) — set nag_scale to ~1.1-1.5 for real negative-prompt influence without a second "
                           "forward pass. Redundant with (and unvalidated alongside) real CFG (guidance > 1.0), which "
                           "already runs a full negative-conditioned pass -- leave at 1.0 on a CFG profile.",
                           required=False, min_value=1.0, max_value=20.0),
            PipeConfigSpec("nag_tau", float, 3.5, "NAG norm-clamp threshold (paper default 3.5)", required=False,
                           min_value=0.1, max_value=20.0),
            PipeConfigSpec("nag_alpha", float, 0.5, "NAG blend-back-toward-positive weight (paper default 0.5)", required=False,
                           min_value=0.0, max_value=1.0),
            PipeConfigSpec("refine_tail", str, "", "Whole-frame enhance (img2img only): walk only the tail of the "
                           "official fixed-mu 8-step Euler grid instead of a denoise-truncated schedule. '' = off, "
                           "'subtle'/'balanced'/'strong' = the last 2/3/4 grid steps.",
                           required=False, choices=["", "subtle", "balanced", "strong"]),
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
            PipeInputSpec("MODELS", IOType.SERVICE, False,
                          "Model lifecycle service, to release the idle TE's VRAM before sampling", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec("image", IOType.IMAGE, "Generated images", is_array=True)]
