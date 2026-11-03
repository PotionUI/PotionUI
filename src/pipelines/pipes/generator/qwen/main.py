"""Generator for the native Qwen-Image family (txt2img / img2img / edit).

Consumes the ``model`` bundle from ``model_loader/qwen`` and the ``conditioning``
list from the shared ``prompt_encoder`` pipe (the mandated pipe path — this pipe
never re-encodes prompts). Per seed it builds a ``Conditioning`` from the
``ConditioningModel`` role dicts, runs ``NativeGenerator.sample`` (flow-matching
denoise with the preset's sampler and TRUE classifier-free guidance — the
Qwen-Image spec's guidance mode is ``"cfg"``, so the sampler runs a cond + uncond
pass), decodes, and emits the image through the shared gallery path.

Unlike Flux's embedded (distilled) guidance, ``cfg_scale`` here is the Qwen
"true_cfg_scale" (~4.0), and the generator MUST pass a real uncond — hence the
conditioning is built with ``n_embeds`` (the encoded negative prompt).

``build_context``/``generate_one`` (shared by every native flow-matching family)
live in ``FlowMatchGeneratorPipe``; this module carries Qwen-Image's own config
schema/defaults, PLUS the ``edit`` mode (Qwen-Image-Edit):

Unlike ``img2img`` (a truncated-schedule denoise of a NOISED copy of the source),
``edit`` runs a full generation from noise whose text conditioning was already
vision-grounded on the source (``model_loader/qwen``'s ``vision: true`` + the
edit preset's ``prompt_encoder`` wiring an ``image`` input — see ``qwen_clip.py``)
and whose DiT call ALSO receives the source's clean VAE latent as
``ref_latents`` — the checkpoint's in-context edit path (``QwenImageDiT.forward``,
dormant until this stage). Mechanism cross-checked against ComfyUI's
``TextEncodeQwenImageEdit`` + ``comfy/ldm/qwen_image/model.py``: the source is
resized to a fixed ~1 megapixel AREA TARGET (aspect preserved, scaled up or
down to hit it, not merely capped) — deliberately NOT
``Img2ImgGeneratorMixin._img2img_target_size`` (that helper reads
``ctx.extra["width"/"height"]``, which come from the generic ``resolution``
config every mode shares; edit mode declares no ``resolution`` field of its
own, but a stray value on that shared key must not silently steer the resize
anyway — ``_edit_target_size`` below hardcodes the area target instead).

"""

from __future__ import annotations

from typing import List


from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec
from src.pipelines.pipes._shared.generation.flow_generator_pipe import FlowMatchGeneratorPipe, iterate_mode_config_specs
from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes._shared.generation.qwen_edit import QwenEditMixin
from src.pipelines.pipes._shared.generation.guidance_options import (
    apply_schedule_settings,
    schedule_settings_config_specs,
)
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes._shared.generation.config_specs import (
    device_config_spec,
    preview_config_spec,
    preview_decoder_config_spec,
    seed_config_spec,
)


class GeneratorQwenPipe(QwenEditMixin, FlowMatchGeneratorPipe):
    name = "generator"
    description = "Native Qwen-Image generator (flow matching, true CFG)"
    family_tag = "GENERATOR QWEN"

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "txt2img", "Generation mode", required=True,
                           choices=["txt2img", "img2img", "edit"]),
            *cls.img2img_config_specs(default_denoise=0.55),
            PipeConfigSpec("steps", int, 20, "Denoising steps", required=False, min_value=1, max_value=100),
            PipeConfigSpec("guidance", float, 4.0, "True CFG scale (Qwen true_cfg_scale)", required=False,
                           min_value=0.0, max_value=30.0),
            PipeConfigSpec("shift", float, None, "Sigma-shift override; blank -> spec default (1.15)", required=False),
            PipeConfigSpec("sampler", str, "euler", "Sampler: any key registered on the sampler registry "
                           "(see GET /api/sampling/catalog)", required=False),
            PipeConfigSpec("resolution", str, "1024x1024", "Resolution (WxH)", required=False),
            PipeConfigSpec("fit", str, "match", "Edit output size: match the first image, or crop/pad it to the resolution",
                           required=False, choices=["match", "crop", "pad"]),
            PipeConfigSpec("quantity", int, 1, "Number of images", required=False, min_value=1, max_value=10),
            seed_config_spec(),
            device_config_spec(),
            preview_config_spec(),
            preview_decoder_config_spec(),
            *schedule_settings_config_specs(),
            # Only txt2img engages this (see NativeGenerator._plan_warm_start) --
            # img2img/edit always pass init_latent and silently fall back to a
            # cold run, same as an ineligible sampler.
            *iterate_mode_config_specs(),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "Qwen-Image model bundle", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning (per image)", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
            cls.img2img_input_spec(),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Generated images", is_array=True),
        ]

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        ctx = super().build_context(pipe_input)
        apply_schedule_settings(ctx, self.config)
        return ctx

    def generate_one(self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter):
        edit = self.maybe_edit(ctx, index, seed, progress)
        if edit is not None:
            return edit
        return super().generate_one(ctx, index, seed, progress)
