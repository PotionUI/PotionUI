"""Native YuE2-3B text-to-music generator."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional

import torch

from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutput, PipeOutputSpec, PipeConfigSpec
from src.pipelines.outputs import AudioGenerationOutput, GalleryGenerationOutput
from src.pipelines.pipes._shared.generation.generator_base import BaseGeneratorPipe, GeneratorContext
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes._shared.generation.seed_plan import plan_seeds
from src.platform.runtime.native.arch.yue2 import ar_loop, nar, protocol
from src.platform.runtime.native.errors import SamplingCancelled

VAE_DECODE_CHUNK_LATENTS = 192
VAE_DECODE_OVERLAP_LATENTS = 64
FPS = 25.0
MAX_DURATION = 360.0
MAX_SEMANTIC_TOKENS = int(MAX_DURATION * FPS)

_AR_PROGRESS_MIN_INTERVAL = 12


@dataclass
class _YuE2Ctx:
    bundle: Any
    style: str
    lyrics: str
    cot: str
    abc: str
    max_tokens: int
    auto_duration: bool
    cfg_scale: float
    nar_steps: int
    temperature: float
    top_p: float
    top_k: int
    repetition_penalty: float
    device: str

    def release_gpu(self) -> None:
        """Best-effort GPU cleanup on a failed generation (duck-typed hook
        `BaseGeneratorPipe._release_gpu_on_error` looks for)."""
        for component in (self.bundle.lm, self.bundle.vae):
            if component is None:
                continue
            try:
                component.offload()
            except Exception:
                pass


class GeneratorAudioYuE2Pipe(BaseGeneratorPipe):
    name = "generator"
    description = "Native YuE2-3B text-to-music generator"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "style": "",
            "lyrics": "",
            "cot": "off",
            "abc": "",
            "duration": 0.0,
            "seed": -1,
            "cfg_scale": None,
            "nar_steps": 32,
            "temperature": protocol.SEMANTIC_SAMPLING_DEFAULTS.temperature,
            "top_p": protocol.SEMANTIC_SAMPLING_DEFAULTS.top_p,
            "top_k": protocol.SEMANTIC_SAMPLING_DEFAULTS.top_k,
            "repetition_penalty": protocol.SEMANTIC_SAMPLING_DEFAULTS.repetition_penalty,
            "device": "cuda",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("style", str, "", "The song's style -- genre/mood/instrumentation/vocals, "
                           "composed by the preset from its form fields", required=True),
            PipeConfigSpec("lyrics", str, "", "Lyrics with [tag] structure markers, one per line "
                           "(e.g. '[instrumental]' for no vocals)", required=False),
            PipeConfigSpec("cot", str, "off", "Chain-of-thought stage: 'off' generates codec tokens "
                           "directly, 'melody'/'full' first write an ABC transcription the codec "
                           "stage conditions on", required=False, choices=["off", "melody", "full"]),
            PipeConfigSpec("abc", str, "", "A user-supplied ABC transcription to condition on directly, "
                           "skipping the model's own ABC-writing stage. Only meaningful with cot != 'off'",
                           required=False),
            PipeConfigSpec(
                "duration", float, 0.0,
                f"Upper bound on song length in seconds; the AR stage may stop earlier and report the "
                f"actual length. 0 = auto (run to the model's own stop token). Hard cap {MAX_DURATION:.0f}s",
                required=False, min_value=0.0, max_value=MAX_DURATION,
            ),
            PipeConfigSpec("seed", int, -1, "Random seed", required=False, min_value=-1),
            PipeConfigSpec("cfg_scale", float, None,
                           "Classifier-free guidance for the semantic (codec) AR stage. "
                           "None or 0 = auto: 1.01 when cot='off', 1.0 otherwise",
                           required=False, min_value=0.0, max_value=20.0),
            PipeConfigSpec("nar_steps", int, 32, "Flow-matching midpoint-Euler steps per acoustic window",
                           required=False, min_value=1, max_value=100),
            PipeConfigSpec("temperature", float, protocol.SEMANTIC_SAMPLING_DEFAULTS.temperature,
                           "Semantic-stage sampling temperature", required=False, min_value=0.0, max_value=5.0),
            PipeConfigSpec("top_p", float, protocol.SEMANTIC_SAMPLING_DEFAULTS.top_p,
                           "Semantic-stage nucleus sampling", required=False, min_value=0.0, max_value=1.0),
            PipeConfigSpec("top_k", int, protocol.SEMANTIC_SAMPLING_DEFAULTS.top_k,
                           "Semantic-stage top-k restriction", required=False, min_value=1, max_value=1024),
            PipeConfigSpec("repetition_penalty", float, protocol.SEMANTIC_SAMPLING_DEFAULTS.repetition_penalty,
                           "Semantic-stage repetition penalty", required=False, min_value=0.01, max_value=2.0),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
        ]

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> None:
        style = str(config.get("style") or "").strip()
        if not style:
            raise ValueError(
                "generator/audio_yue2: 'style' cannot be empty -- there is nothing to generate music from"
            )
        cot = config.get("cot", "off")
        if cot not in protocol.INSTRUCTIONS:
            raise ValueError(f"generator/audio_yue2: 'cot' must be one of off/melody/full, got {cot!r}")
        abc = str(config.get("abc") or "").strip()
        if abc and cot == "off":
            raise ValueError(
                "generator/audio_yue2: 'abc' requires cot != 'off' -- there is no ABC stage to condition "
                "when cot is 'off'"
            )
        duration = float(config.get("duration", 0.0))
        if duration < 0:
            raise ValueError(
                f"generator/audio_yue2: 'duration' must be positive (or 0 for auto -- generate until "
                f"the model ends the song), got {duration}"
            )
        if duration > MAX_DURATION:
            raise ValueError(
                f"generator/audio_yue2: 'duration' ({duration}) exceeds the {MAX_DURATION:.0f}s hard cap"
            )

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "YuE2-3B model bundle", is_array=False),
            PipeInputSpec("MODELS", IOType.SERVICE, False,
                          "Model lifecycle service, to release the AR/NAR backbone before the VAE places",
                          is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("audio", IOType.AUDIO, "Generated song(s), one per seed", is_array=True),
        ]

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        bundle = pipe_input.input["model"]
        if bundle.spec.family != "yue2":
            raise ValueError(
                f"generator/audio_yue2: loaded model '{bundle.spec.family}/{bundle.spec.variant}' "
                f"is not a YuE2 checkpoint. Pick a YuE2 model for this preset."
            )
        duration = float(self.config.get("duration", 0.0))
        max_tokens = (
            MAX_SEMANTIC_TOKENS if duration == 0
            else min(MAX_SEMANTIC_TOKENS, max(1, int(round(duration * FPS))))
        )
        cot = str(self.config.get("cot", "off"))
        return GeneratorContext(
            quantity=1,
            input_seeds=None,
            extra=_YuE2Ctx(
                bundle=bundle,
                style=str(self.config.get("style") or ""),
                lyrics=str(self.config.get("lyrics") or ""),
                cot=cot,
                abc=str(self.config.get("abc") or ""),
                max_tokens=max_tokens,
                auto_duration=duration == 0,
                cfg_scale=protocol.guidance_scale(cot, self.config.get("cfg_scale") or None),
                nar_steps=int(self.config.get("nar_steps", 32)),
                temperature=float(self.config.get("temperature", protocol.SEMANTIC_SAMPLING_DEFAULTS.temperature)),
                top_p=float(self.config.get("top_p", protocol.SEMANTIC_SAMPLING_DEFAULTS.top_p)),
                top_k=int(self.config.get("top_k", protocol.SEMANTIC_SAMPLING_DEFAULTS.top_k)),
                repetition_penalty=float(self.config.get(
                    "repetition_penalty", protocol.SEMANTIC_SAMPLING_DEFAULTS.repetition_penalty,
                )),
                device=str(self.config.get("device", "cuda")),
            ),
        )

    def generate_one(
        self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter,
        is_cancelled: Optional[callable] = None,
    ) -> AudioGenerationOutput:
        c: _YuE2Ctx = ctx.extra
        bundle = c.bundle
        models = getattr(self, "_models", None)

        lm_model = bundle.lm
        tokenizer = bundle.tokenizer
        if tokenizer is None:
            raise ValueError(
                "generator/audio_yue2: the model bundle's AR/NAR backbone is not resident "
                "(already released) -- this pipe must run before anything evicts it"
            )

        instruction = protocol.INSTRUCTIONS[c.cot]
        base_prompt_ids = protocol.build_prompt_ids(tokenizer.encode, instruction, c.style, c.lyrics, cot=c.cot)
        protocol.ensure_prompt_fits(
            base_prompt_ids, c.max_tokens, context=lm_model.module.cfg.max_position_embeddings,
        )

        def cancelled() -> bool:
            return bool(is_cancelled and is_cancelled())

        ar_last_emit = -_AR_PROGRESS_MIN_INTERVAL

        def on_semantic_frame(i: int, total: int) -> None:
            nonlocal ar_last_emit
            if i - ar_last_emit >= _AR_PROGRESS_MIN_INTERVAL or i >= total:
                progress.step(i, total, state="composing")
                ar_last_emit = i

        lm_model.move_to(c.device)
        try:
            if c.cot == "off":
                semantic_prefix_ids = base_prompt_ids
                negative_abc_ids = None
            elif c.abc:
                semantic_prefix_ids = protocol.build_prompt_ids(
                    tokenizer.encode, instruction, c.style, c.lyrics, abc=c.abc, cot=c.cot,
                )
                negative_abc_ids = tokenizer.encode(c.abc)
            else:
                abc_last_emit = -_AR_PROGRESS_MIN_INTERVAL

                def on_abc_frame(i: int, total: int) -> None:
                    nonlocal abc_last_emit
                    if i - abc_last_emit >= _AR_PROGRESS_MIN_INTERVAL or i >= total:
                        progress.step(i, total, state="transcribing")
                        abc_last_emit = i

                abc_ids, abc_stopped = ar_loop.generate(
                    lm_model.module, base_prompt_ids, protocol.ABC_SAMPLING_DEFAULTS, seed, phase="abc",
                    is_cancelled=cancelled, on_frame=on_abc_frame,
                )
                if not abc_stopped:
                    raise ValueError(
                        "generator/audio_yue2: the ABC transcription stage hit its token budget "
                        "without reaching its end token -- no music was generated for this seed"
                    )
                progress.step(len(abc_ids), len(abc_ids), state="transcribing")
                semantic_prefix_ids = base_prompt_ids + abc_ids + [protocol.ABC_END, protocol.MUSIC_START]
                negative_abc_ids = abc_ids

            negative_ids = None
            if c.cfg_scale != 1.0:
                negative_ids = protocol.build_negative_prompt_ids(tokenizer.encode, c.cot, abc_ids=negative_abc_ids)

            semantic_sampling = replace(
                protocol.SEMANTIC_SAMPLING_DEFAULTS,
                temperature=c.temperature, top_p=c.top_p, top_k=c.top_k,
                repetition_penalty=c.repetition_penalty, max_tokens=c.max_tokens,
                min_tokens=min(protocol.SEMANTIC_SAMPLING_DEFAULTS.min_tokens, c.max_tokens),
            )
            codec_ids_offset, stopped = ar_loop.generate(
                lm_model.module, semantic_prefix_ids, semantic_sampling, seed, phase="semantic",
                negative_ids=negative_ids, cfg_scale=c.cfg_scale, is_cancelled=cancelled,
                on_frame=on_semantic_frame,
            )
            if not codec_ids_offset:
                raise ValueError(
                    "generator/audio_yue2: the AR stage stopped at frame 0 -- no audio was "
                    "generated for this seed"
                )
            if not stopped and c.auto_duration:
                raise ValueError(
                    "generator/audio_yue2: the AR stage hit its token budget without reaching its "
                    "end token -- the model never closed the song at duration=auto"
                )
            progress.step(len(codec_ids_offset), len(codec_ids_offset), state="composing")
            codec_ids = [token_id - protocol.CODEC_OFFSET for token_id in codec_ids_offset]

            def on_nar_step(step: int, total: int) -> None:
                progress.step(step, total, state="rendering")

            latents = nar.synthesize(
                lm_model.module, semantic_prefix_ids, codec_ids, seed, steps=c.nar_steps,
                context=lm_model.module.cfg.max_position_embeddings, is_cancelled=cancelled,
                on_step=on_nar_step,
            )
        finally:
            lm_model.offload()

        self._release_lm(bundle, models)
        del lm_model

        if is_cancelled and is_cancelled():
            raise SamplingCancelled()

        vae_model = bundle.vae
        vae_model.move_to(c.device)
        try:
            channels_first = latents.permute(0, 2, 1).to(c.device)
            waveform = vae_model.module.decode(channels_first, chunk_size=VAE_DECODE_CHUNK_LATENTS, overlap=VAE_DECODE_OVERLAP_LATENTS)
        finally:
            vae_model.offload()

        sample_rate = int(vae_model.module.sample_rate)
        audio_path = self._write_wav(waveform, sample_rate)
        duration_seconds = len(codec_ids) / FPS

        return AudioGenerationOutput(
            audio_path=audio_path, temporary=False, track_type="mixed", seed=seed,
            duration=duration_seconds, sample_rate=sample_rate,
            channels=int(waveform.shape[1]), guidance_scale=c.cfg_scale,
        )

    @staticmethod
    def _release_lm(bundle: Any, models: Any) -> None:
        key = getattr(bundle, "lm_cache_key", None)
        if not key or models is None:
            return
        evict = getattr(models, "evict_dead_weight", None)
        if callable(evict):
            evict(key)

    @staticmethod
    def _write_wav(waveform: torch.Tensor, sample_rate: int) -> Path:
        """The first item of a `(B, 2, T)` float32 batch, as a .wav on disk."""
        import numpy as np
        import soundfile as sf

        samples = waveform[0].detach().to(device="cpu", dtype=torch.float32).clamp(-1.0, 1.0).numpy()
        samples = np.ascontiguousarray(samples.T)
        out_path = Path(tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name)
        sf.write(str(out_path), samples, sample_rate)
        return out_path

    def emit_results(self, generation_outputs: callable, results: List[Any], used_seeds: List[int]) -> None:
        generation_outputs(GalleryGenerationOutput(images=[], audios=list(results)))

    def build_output(self, results: List[Any]) -> Dict[str, Any]:
        return {"audio": [r.audio_path for r in results]}

    def process(
        self, pipe_input: PipeInput, generation_outputs: callable,
        is_cancelled: Optional[callable] = None,
    ) -> PipeOutput:
        ctx = self.build_context(pipe_input)
        self._models = pipe_input.input.get("MODELS")
        seeds = plan_seeds(ctx.input_seeds, int(self.config.get("seed", -1)), ctx.quantity)
        progress = ProgressEmitter(generation_outputs, title=self.name)

        results: List[Any] = []
        used_seeds: List[int] = []
        try:
            for i, seed in enumerate(seeds):
                if is_cancelled and is_cancelled():
                    break
                results.append(self.generate_one(ctx, i, seed, progress, is_cancelled))
                used_seeds.append(seed)
        except Exception:
            self._release_gpu_on_error(ctx)
            raise

        self.emit_results(generation_outputs, results, used_seeds)
        return PipeOutput(output=self.build_output(results))
