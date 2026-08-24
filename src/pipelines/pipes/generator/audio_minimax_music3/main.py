# Derived from: diffusers `src/diffusers/modular_pipelines/minimax_music3/decoders.py`
# (Apache-2.0, "Copyright 2026 The MiniMax Team and The HuggingFace Team") for the
# per-window vocode-then-crop-then-concat decode recipe (crop the DECODED waveform
# by `crop_bounds() * hop_length` samples, not the pre-decode latent -- see
# `_decode_windows`'s docstring).
"""Native MiniMax-Music3 text-to-music generator.

One song per generation: assemble the checkpoint's special-token prompt from
`caption`/`lyrics`, run the autoregressive semantic+residual-code stage
(`arch/minimax_music3/ar_loop.py`), hand its `frame_hiddens` to the windowed
flow-matching loop (`arch/minimax_music3/flow.py`), vocode each window through
the DAV and stitch the result into one WAV.

**Stage handoff (port plan S5 "VRAM strategy", risk #2).** The AR stage's LM
unit and the flow stage's DiT are never meant to be resident together on a
24GB card. This pipe moves the LM to `device` for the AR loop, offloads it
back to CPU when the loop ends, then EXPLICITLY evicts its MODELS cache entry
and drops this frame's own strong reference to it (`del lm_model`) BEFORE
placing the DiT -- offloading alone is not enough (the same failure shape as
the H3 mode-switch RAM OOM, `h3_mode_switch_ram_oom.md`: lazy/eventual
eviction is not proactive enough to guarantee the LM is gone before the DiT's
placement call runs).

**No `prompt_encoder`/`clip` stage.** Unlike every other native family, this
one never produces a `ConditioningModel`: the tokenizer lives on the loaded
bundle itself (`bundle.tokenizer`, built alongside the LM at load time -- see
`model_loader/minimax_music3/te_loader.py`), and the AR core's output IS the
flow stage's conditioning, produced fresh inside this same pipe on every
generation. There is nothing a `prompt_encoder`-style cache could reuse across
different seeds (the AR loop's output depends on the sampled tokens).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import torch

from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutput, PipeOutputSpec, PipeConfigSpec, logger
from src.pipelines.outputs import AudioGenerationOutput, GalleryGenerationOutput
from src.pipelines.pipes._shared.generation.generator_base import BaseGeneratorPipe, GeneratorContext
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes._shared.generation.seed_plan import plan_seeds
from src.platform.runtime.native.arch.minimax_music3 import ar_loop, flow
from src.platform.runtime.native.optimizations.compile import maybe_compile_music3_ar

FPS = 25.0
DEFAULT_DURATION = 60.0
MAX_DURATION = 360.0
MAX_FRAMES = int(MAX_DURATION * FPS)  # 9000, matches ar_loop.MAX_AUDIO_FRAMES

# Raw AR events arrive at 25/s; the progress bus does not need more than
# ~2 updates/second (port plan risk #7 -- 9000 raw events would flood
# /ws/generation over a 6-minute song). ~12 frames is ~2Hz at 25fps.
_AR_PROGRESS_MIN_INTERVAL_FRAMES = 12


@dataclass
class _Music3Ctx:
    bundle: Any
    caption: str
    lyrics: str
    max_frames: int
    steps: int
    ar_cfg_scale: float
    cfg_scale: float
    top_k: int
    device: str

    def release_gpu(self) -> None:
        """Best-effort GPU cleanup on a failed generation (duck-typed hook
        `BaseGeneratorPipe._release_gpu_on_error` looks for)."""
        for component in (self.bundle.dit, self.bundle.lm, self.bundle.dav):
            if component is None:
                continue
            try:
                component.offload()
            except Exception:
                pass


def _decode_windows(dav_module: Any, latent_chunks: List[torch.Tensor], device: str) -> torch.Tensor:
    """Vocode each flow-matching window independently, crop the DECODED
    waveform (not the pre-decode latent -- see this module's `# Derived
    from:` note) by `crop_bounds() * hop_length` samples, and concatenate.

    Decoding each window on its own (rather than concatenating latents first
    and decoding once) matters: a window's own decode has correct
    convolutional context throughout its own span, and `crop_bounds` throws
    away exactly the edge region that instead lacks true neighbouring-window
    context -- the reference's own overlap-and-discard scheme, reproduced
    here in sample space via the vocoder's `hop_length`.
    """
    hop = int(dav_module.hop_length)
    num_windows = len(latent_chunks)
    chunks: List[torch.Tensor] = []
    for window_index, latents in enumerate(latent_chunks):
        waveform = dav_module.decode(latents.to(device))
        left, right = flow.crop_bounds(window_index, num_windows)
        left_samples = left * hop
        right_samples = right * hop
        end = waveform.shape[-1] - right_samples if right_samples else None
        chunks.append(waveform[..., left_samples:end])
    return torch.cat(chunks, dim=-1)


class GeneratorAudioMinimaxMusic3Pipe(BaseGeneratorPipe):
    name = "generator"
    description = "Native MiniMax-Music3 text-to-music generator"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "caption": "",
            "lyrics": "",
            "duration": DEFAULT_DURATION,
            "steps": 30,
            "seed": -1,
            "ar_cfg_scale": 1.5,
            "cfg_scale": 1.7,
            "top_k": 50,
            "device": "cuda",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("caption", str, "", "The song's caption -- genre/mood/instrumentation/vocals, "
                           "composed by the preset from its form fields", required=True),
            PipeConfigSpec("lyrics", str, "", "Lyrics with [tag] structure markers, one per line "
                           "(e.g. '[instrumental]' for no vocals)", required=False),
            PipeConfigSpec(
                "duration", float, DEFAULT_DURATION,
                f"Upper bound on song length in seconds; the AR stage may stop earlier and report the "
                f"actual length. 0 = auto (run to the model's own stop token). Hard cap {MAX_DURATION:.0f}s",
                required=False, min_value=0.0, max_value=MAX_DURATION,
            ),
            PipeConfigSpec("steps", int, 30, "Flow-matching Euler steps per denoising window",
                           required=False, min_value=1, max_value=100),
            PipeConfigSpec("seed", int, -1, "Random seed (drives both the AR stage and every flow window)",
                           required=False, min_value=-1),
            PipeConfigSpec("ar_cfg_scale", float, 1.5,
                           "Classifier-free guidance for the AR (semantic + residual code) sampling stage",
                           required=False, min_value=1.0, max_value=10.0),
            PipeConfigSpec("cfg_scale", float, 1.7, "Classifier-free guidance for the flow-matching DiT",
                           required=False, min_value=1.0, max_value=10.0),
            PipeConfigSpec("top_k", int, 50, "Top-k restriction for AR sampling",
                           required=False, min_value=1, max_value=1024),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
        ]

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> None:
        caption = str(config.get("caption") or "").strip()
        if not caption:
            raise ValueError(
                "generator/audio_minimax_music3: 'caption' cannot be empty -- there is nothing to "
                "generate music from"
            )
        duration = float(config.get("duration", DEFAULT_DURATION))
        if duration < 0:
            raise ValueError(
                f"generator/audio_minimax_music3: 'duration' must be positive (or 0 for auto -- "
                f"generate until the model ends the song), got {duration}"
            )
        if duration > MAX_DURATION:
            raise ValueError(
                f"generator/audio_minimax_music3: 'duration' ({duration}) exceeds the "
                f"{MAX_DURATION:.0f}s hard cap"
            )

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "MiniMax-Music3 model bundle", is_array=False),
            PipeInputSpec("MODELS", IOType.SERVICE, False,
                          "Model lifecycle service, to release the AR core before the DiT places",
                          is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("audio", IOType.AUDIO, "Generated song(s), one per seed", is_array=True),
        ]

    # -- context ---------------------------------------------------------

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        bundle = pipe_input.input["model"]
        if bundle.spec.family != "minimax_music3":
            raise ValueError(
                f"generator/audio_minimax_music3: loaded model '{bundle.spec.family}/{bundle.spec.variant}' "
                f"is not a MiniMax-Music3 checkpoint. Pick a MiniMax-Music3 DiT for this preset."
            )
        duration = float(self.config.get("duration", DEFAULT_DURATION))
        # duration is a hard frame CAP, never model conditioning (the model has
        # no length input -- it ends a song with its own stop token). 0 = auto:
        # cap at the model max and let the stop token decide.
        max_frames = MAX_FRAMES if duration == 0 else min(MAX_FRAMES, max(1, int(round(duration * FPS))))
        return GeneratorContext(
            quantity=1,
            input_seeds=None,
            extra=_Music3Ctx(
                bundle=bundle,
                caption=str(self.config.get("caption") or ""),
                lyrics=str(self.config.get("lyrics") or ""),
                max_frames=max_frames,
                steps=int(self.config.get("steps", 30)),
                ar_cfg_scale=float(self.config.get("ar_cfg_scale", 1.5)),
                cfg_scale=float(self.config.get("cfg_scale", 1.7)),
                top_k=int(self.config.get("top_k", 50)),
                device=str(self.config.get("device", "cuda")),
            ),
        )

    # -- per-seed generation -----------------------------------------------

    def generate_one(
        self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter,
        is_cancelled: Optional[callable] = None,
    ) -> AudioGenerationOutput:
        c: _Music3Ctx = ctx.extra
        bundle = c.bundle
        models = getattr(self, "_models", None)

        tokenizer = bundle.tokenizer
        if tokenizer is None:
            raise ValueError(
                "generator/audio_minimax_music3: the model bundle's text encoder is not resident "
                "(already released) -- this pipe must run before anything evicts it"
            )
        input_ids = tokenizer.build_conditional_pair(c.caption, c.lyrics)

        lm_model = bundle.lm
        warning = ar_loop.position_budget_warning(
            int(input_ids.shape[1]), c.max_frames, lm_model.module.cfg.max_position_embeddings,
        )
        if warning:
            progress.state(warning)
            logger.warning("[GENERATOR MINIMAX-MUSIC3] %s", warning)

        generator = torch.Generator(device=c.device).manual_seed(int(seed))

        def cancelled() -> bool:
            return bool(is_cancelled and is_cancelled())

        # -- AR stage: semantic + residual codes, one frame at a time --------
        last_emit = -_AR_PROGRESS_MIN_INTERVAL_FRAMES

        def on_frame(i: int, total: int) -> None:
            nonlocal last_emit
            if i - last_emit >= _AR_PROGRESS_MIN_INTERVAL_FRAMES or i >= total:
                progress.step(i, total, state="composing")
                last_emit = i

        lm_model.move_to(c.device)
        try:
            # Same gated, reversible mechanism the DiT path uses
            # (optimizations/compile.py); undone automatically by
            # `lm_model.offload()` below (NativeModel.move_to("cpu") restores
            # any `_compiled` handle before the module leaves the GPU).
            maybe_compile_music3_ar(lm_model, resident=True, is_cuda=str(c.device).startswith("cuda"))
            frame_hiddens = ar_loop.generate(
                lm_model.module, input_ids.to(c.device), generator, c.max_frames,
                cfg_scale=c.ar_cfg_scale, top_k=c.top_k, on_frame=on_frame, is_cancelled=cancelled,
            )
        finally:
            lm_model.offload()

        num_frames = int(frame_hiddens.shape[1])
        if num_frames == 0:
            raise ValueError(
                "generator/audio_minimax_music3: the AR stage stopped at frame 0 -- no audio was "
                "generated for this seed"
            )

        # Release the AR core's MODELS cache entry, AND this frame's own
        # strong reference to it, before the DiT places (see module
        # docstring). Offloading alone is not enough: `lm_model` staying in
        # scope for the rest of this call would keep the LM RAM-resident
        # right through the flow stage regardless of what the cache does.
        self._release_lm(bundle, models)
        del lm_model

        # -- Flow-matching stage: windowed euler denoise ----------------------
        dit_model = bundle.dit
        dit_model.move_to(c.device)
        try:
            latent_chunks = flow.denoise_windowed(
                dit_model.module, frame_hiddens, steps=c.steps, cfg_scale=c.cfg_scale,
                generator=generator, device=c.device, dtype=dit_model.compute_dtype,
                on_step=lambda step, total: progress.step(step, total, state="rendering audio"),
                is_cancelled=cancelled,
            )
        finally:
            dit_model.offload()

        # -- DAV decode ---------------------------------------------------------
        progress.state("Decoding audio")
        dav_model = bundle.dav
        dav_model.move_to(c.device)
        try:
            waveform = _decode_windows(dav_model.module, latent_chunks, c.device)
        finally:
            dav_model.offload()

        sample_rate = int(dav_model.module.sample_rate)
        audio_path = self._write_wav(waveform, sample_rate)
        duration_seconds = num_frames / FPS

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
        """The first item of a `(B, 2, T)` float32 batch, as a .wav on disk.

        `soundfile`, not `torchaudio.save` -- same rationale as the Stable
        Audio 3 plugin pipe's `_write_wav` (torchaudio 2.11's save entry
        point needs TorchCodec, which is not installed here).
        """
        import numpy as np
        import soundfile as sf

        samples = waveform[0].detach().to(device="cpu", dtype=torch.float32).clamp(-1.0, 1.0).numpy()
        samples = np.ascontiguousarray(samples.T)  # (channels, T) -> (T, channels)
        out_path = Path(tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name)
        sf.write(str(out_path), samples, sample_rate)
        return out_path

    # -- emission ----------------------------------------------------------

    def emit_results(self, generation_outputs: callable, results: List[Any], used_seeds: List[int]) -> None:
        # The core `gallery` pipe takes only IOType.IMAGE/VIDEO, so an audio
        # pipeline builds its own GalleryGenerationOutput here -- same
        # arrangement the Stable Audio 3 plugin pipe uses.
        generation_outputs(GalleryGenerationOutput(images=[], audios=list(results)))

    def build_output(self, results: List[Any]) -> Dict[str, Any]:
        return {"audio": [r.audio_path for r in results]}

    # -- process: overrides BaseGeneratorPipe's to thread `is_cancelled` into
    # `generate_one` (per-frame/per-step cancellation, not just between-seed) --

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
