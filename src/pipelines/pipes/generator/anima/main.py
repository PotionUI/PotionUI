"""Generator for the native Anima family (txt2img).

Consumes the ``model`` bundle from ``model_loader/anima`` and the ``conditioning``
list from the shared ``prompt_encoder`` pipe (the mandated path — this pipe never
re-encodes prompts). Per seed it builds a ``Conditioning`` from the
``ConditioningModel`` role dicts, runs the flow-matching denoise with TRUE CFG
(Anima's spec guidance is ``"cfg"``), decodes through the Wan-2.1 causal-3D VAE,
and emits the image.

Anima's one non-generic piece is conditioning: the DiT needs the Qwen3-0.6B
``context`` PLUS the ``t5xxl_ids`` / ``t5xxl_weights`` that its in-model LLMAdapter
fuses into the cross-attention context. The generic engine ``model_forward`` only
threads ``context``/``y``/``guidance``/``attention_mask``, so this pipe uses a
:class:`AnimaNativeGenerator` whose ``_make_forward`` also passes the two T5
tensors — the DiT does the fusion (and the sigma->timestep x1000 scaling) inside
its ``forward``.

``build_context``/``generate_one`` (shared by every native flow-matching family)
live in ``FlowMatchGeneratorPipe``; this module only carries Anima's own config
schema/defaults plus the ``AnimaNativeGenerator`` subclass.

TE eviction: by generator time ``prompt_encoder`` has already produced the
conditioning every mode needs, so the Qwen3-0.6B TE is dead weight through
sampling and decode. Released via ``bundle.te_cache_key`` +
``models.evict_dead_weight`` -- mirrors ``generator/qwen``'s
``_release_idle_te`` (see that module's docstring for the full rationale).
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.platform.runtime.native.engine import NativeGenerator
from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec, logger
from src.pipelines.pipes._shared.generation.flow_generator_pipe import FlowMatchGeneratorPipe
from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes._shared.generation.native_generator import register_native_generator


@register_native_generator("anima")
class AnimaNativeGenerator(NativeGenerator):
    """``NativeGenerator`` whose ``model_forward`` also threads the LLMAdapter's
    T5 target ids/weights into the Anima DiT (the DiT fuses them with the Qwen3
    context and applies the ``sigma*1000`` timestep scaling itself)."""

    def _make_forward(self, device, dtype):
        module = self.dit.module

        def model_forward(x, sigma, conditioning: dict):
            # FBCache: forward the reserved "step_cache" key (denoise()'s
            # _CachingGuidance injects it per branch) only when present, mirroring
            # the base NativeGenerator._make_forward — the arch honours it in
            # _dit_forward.
            extra = {}
            step_cache = conditioning.get("step_cache")
            if step_cache is not None:
                extra["step_cache"] = step_cache
            return module(
                x,
                sigma,
                conditioning["context"],
                t5xxl_ids=conditioning.get("t5xxl_ids"),
                t5xxl_weights=conditioning.get("t5xxl_weights"),
                **extra,
            )

        return model_forward


class GeneratorAnimaPipe(FlowMatchGeneratorPipe):
    name = "generator"
    description = "Native Anima generator (flow matching, true CFG, LLMAdapter text fusion)"
    family_tag = "GENERATOR ANIMA"

    def _generator_class(self):
        return AnimaNativeGenerator

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        ctx = super().build_context(pipe_input)
        self._release_idle_te(pipe_input)
        return ctx

    def _release_idle_te(self, pipe_input: PipeInput) -> None:
        """Evict the TE's MODELS cache entry -- see the module docstring's "TE
        eviction" section; mirrors ``generator/qwen``'s ``_release_idle_te``
        (same ``bundle.te_cache_key`` + ``models.evict_dead_weight`` mechanism).

        Best-effort and silent: a missing ``te_cache_key`` (a bundle built
        outside the MODELS cache, e.g. isolated pipe tests), a missing
        ``MODELS`` service, or an eviction that raises are all treated as
        "nothing to do" -- this is a VRAM optimisation, never something a
        generation should fail over.
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
            "steps": 24,
            "guidance": 6.0,
            "shift": None,
            "sampler": "euler",
            "resolution": "1024x1024",
            "quantity": 1,
            "seed": -1,
            "device": "cuda",
            "denoise": 0.55,
            "preview": True,
            "step_cache": {},
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "txt2img", "Generation mode", required=True, choices=["txt2img", "img2img"]),
            *cls.img2img_config_specs(default_denoise=0.55),
            PipeConfigSpec("steps", int, 24, "Denoising steps", required=False, min_value=1, max_value=100),
            PipeConfigSpec("guidance", float, 6.0, "True CFG scale", required=False, min_value=0.0, max_value=30.0),
            PipeConfigSpec("shift", float, None, "Sigma-shift override; blank -> spec default (3.0)", required=False),
            PipeConfigSpec("sampler", str, "euler", "Sampler", required=False,
                           choices=["euler", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_3m", "res_multistep", "unipc", "lcm"]),
            PipeConfigSpec("resolution", str, "1024x1024", "Resolution (WxH)", required=False),
            PipeConfigSpec("quantity", int, 1, "Number of images", required=False, min_value=1, max_value=10),
            PipeConfigSpec("seed", int, -1, "Random seed", required=False, min_value=-1),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            PipeConfigSpec("preview", bool, True, "Emit live latent previews to the workbench during sampling", required=False),
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
            PipeInputSpec("model", IOType.MODEL, True, "Anima model bundle", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning (per image)", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
            cls.img2img_input_spec(),
            PipeInputSpec("MODELS", IOType.SERVICE, False,
                          "Model lifecycle service, to release the idle TE's VRAM before sampling", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Generated images", is_array=True),
        ]
