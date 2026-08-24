#!/usr/bin/env python
"""End-to-end smoke harness for the native engine, on GPU or any supported model.

Loads a diffusion model + text encoder(s) + VAE from single safetensors files,
runs one txt2img generation, and saves the PNG plus the final latent (for golden
comparison). Works on CPU or CUDA.

Usage
-----
Klein (Flux2) with random context (no TE needed — validates DiT+VAE):
    python tests/manual/native_smoke.py \
        --dit models/diffusion_models/flux2Klein_9b.safetensors \
        --vae models/vae/flux2-vae.safetensors \
        --random-context --steps 2 --width 128 --height 128 --seed 42 \
        --device cpu --out /tmp/klein.png

Flux1 (T5 + CLIP-L pair) with a real prompt:
    python tests/manual/native_smoke.py \
        --dit <flux1_dit> --te <t5xxl> --te <clip_l> --vae models/vae/ae.sft \
        --prompt "a cat" --steps 20 --width 1024 --height 1024 --device cuda:0 \
        --out out.png

Golden comparison instrumentation:
    ... --dump-latents step0,mid,final     # saves x0 estimates next to --out
    ... --save-noise noise.pt              # save the initial noise tensor
    ... --noise-in noise.pt                # inject a saved noise tensor (match ComfyUI)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Allow running from the repo root without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch  # noqa: E402

from src.platform.runtime.native.engine import (  # noqa: E402
    Conditioning,
    NativeEngineLoader,
    NativeGenerator,
)
from src.platform.runtime.native.memory.device_plan import DevicePlan  # noqa: E402
from src.platform.runtime.native.sampling.hooks import BaseStepHook  # noqa: E402

logger = logging.getLogger("native_smoke")


class _StdoutProgress(BaseStepHook):
    priority = 100

    def on_start(self, total_steps: int) -> None:
        self._t0 = time.perf_counter()
        print(f"[sample] {total_steps} steps", flush=True)

    def on_step(self, step_index, total_steps, x, sigma, denoised_x0) -> None:
        dt = time.perf_counter() - self._t0
        print(f"[sample] step {step_index + 1}/{total_steps}  sigma={sigma:.4f}  "
              f"{dt:.1f}s elapsed  x0_finite={bool(torch.isfinite(denoised_x0).all()) if denoised_x0 is not None else 'n/a'}",
              flush=True)


class _LatentDump(BaseStepHook):
    """Saves x0-estimate latents at requested checkpoints for golden compare."""

    priority = 50

    def __init__(self, which: set[str], out_stem: Path) -> None:
        self.which = which
        self.stem = out_stem
        self.saved: dict[str, Path] = {}

    def on_step(self, step_index, total_steps, x, sigma, denoised_x0) -> None:
        if denoised_x0 is None:
            return
        mid = total_steps // 2
        label = None
        if step_index == 0 and "step0" in self.which:
            label = "step0"
        elif step_index == mid and "mid" in self.which:
            label = "mid"
        elif step_index == total_steps - 1 and "final" in self.which:
            label = "final"
        if label:
            p = self.stem.with_name(f"{self.stem.stem}.{label}.pt")
            torch.save(denoised_x0.detach().cpu(), p)
            self.saved[label] = p
            print(f"[dump] {label} latent -> {p}", flush=True)


def _phase(name: str):
    class _Timer:
        def __enter__(self):
            self.t0 = time.perf_counter()
            print(f"[{name}] ...", flush=True)
            return self

        def __exit__(self, *exc):
            self.dt = time.perf_counter() - self.t0
            print(f"[{name}] done in {self.dt:.1f}s", flush=True)
    return _Timer()


def _random_conditioning(gen: NativeGenerator, batch: int, seq: int, seed: int) -> Conditioning:
    """Seeded random context of the DiT's expected width — bypasses the TE."""
    params = gen.dit.module.params
    g = torch.Generator().manual_seed(seed + 777)
    context = torch.randn(batch, seq, params.context_in_dim, generator=g)
    y = None
    if getattr(params, "vec_in_dim", None):
        y = torch.randn(batch, params.vec_in_dim, generator=g)
    # attention_mask left None: mask *shaping* to the joint txt+img sequence is an
    # open arch contract; None = full attention, which is what a smoke run wants.
    return Conditioning({"context": context, "y": y, "attention_mask": None})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dit", required=True)
    ap.add_argument("--te", action="append", default=[], help="repeatable: 1 for Klein, 2 (t5+clip) for Flux1")
    ap.add_argument("--te-variant", default=None,
                    help="disambiguate a structurally-shared TE (e.g. 'zimage' for Z-Image's "
                         "Qwen3-4B, which is identical to Klein's but uses the penultimate layer)")
    ap.add_argument("--vae", required=True)
    ap.add_argument("--prompt", default="")
    ap.add_argument("--negative", default=None)
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--cfg", "--guidance", dest="cfg", type=float, default=3.5)
    ap.add_argument("--sampler", default="euler")
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--out", default="out.png")
    ap.add_argument("--tier-vram", type=float, default=None, help="override detected VRAM budget (GB)")
    ap.add_argument("--random-context", action="store_true", help="skip TE, feed seeded random context")
    ap.add_argument("--context-len", type=int, default=256, help="seq length for --random-context")
    ap.add_argument("--dump-latents", default="", help="comma list of step0,mid,final")
    ap.add_argument("--save-noise", default=None, help="save the initial noise tensor to this .pt")
    ap.add_argument("--noise-in", default=None, help="load an initial noise tensor from this .pt")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    device = args.device
    is_cuda = device.startswith("cuda") and torch.cuda.is_available()
    if is_cuda:
        torch.cuda.reset_peak_memory_stats()

    loader = NativeEngineLoader(device=device, vram_gb=args.tier_vram)

    with _phase("load-dit"):
        dit = loader.load(args.dit, "diffusion_model")
    print(f"  DiT: {dit.spec.family}/{dit.spec.variant}  ~{dit.estimated_vram_gb:.1f}GB  "
          f"quant={dit.quant_format}  compute={dit.compute_dtype}", flush=True)

    te = None
    if not args.random_context:
        if not args.te:
            ap.error("provide --te (1 for Klein, 2 for Flux1) or use --random-context")
        te_arg = args.te if len(args.te) > 1 else args.te[0]
        with _phase("load-te"):
            if args.te_variant:
                # Structurally-shared TE: select the encode contract explicitly
                # (NativeEngineLoader.load's TE path has no variant hint).
                from src.platform.runtime.native.text_encoders.loader import load_text_encoder
                te = load_text_encoder(te_arg, device="cpu", te_variant=args.te_variant)
            else:
                te = loader.load(te_arg, "text_encoder").module

    with _phase("load-vae"):
        vae = loader.load(args.vae, "vae")

    device_plan = DevicePlan(device, device, device)
    # --tier-vram plumbs into the generator's placement (phase sequencing),
    # not just the loader's ops selection.
    gen = NativeGenerator(dit, te, vae, device_plan, vram_gb=args.tier_vram)

    latents_shape = gen.latent_shape_for(args.width, args.height)
    print(f"  latent shape: {latents_shape}", flush=True)

    with _phase("encode"):
        if args.random_context:
            conditioning = _random_conditioning(gen, latents_shape[0], args.context_len, args.seed)
        else:
            conditioning = gen.encode_prompt(args.prompt, args.negative)

    # Initial noise: inject a saved tensor (golden compare) or draw it here so we
    # can also save it. Owning noise generation in the harness keeps save/inject
    # symmetric across a ComfyUI reference run.
    if args.noise_in:
        noise = torch.load(args.noise_in, map_location="cpu")
        print(f"  injected noise from {args.noise_in}", flush=True)
    else:
        g = torch.Generator().manual_seed(args.seed)
        noise = torch.randn(latents_shape, generator=g)
    if args.save_noise:
        torch.save(noise.cpu(), args.save_noise)
        print(f"  saved noise -> {args.save_noise}", flush=True)

    out_path = Path(args.out)
    hooks: list = [_StdoutProgress()]
    dumper = None
    if args.dump_latents:
        dumper = _LatentDump({s.strip() for s in args.dump_latents.split(",") if s.strip()}, out_path)
        hooks.append(dumper)

    with _phase("sample"):
        latent = gen.sample(
            conditioning, latents_shape, steps=args.steps, seed=args.seed,
            cfg_scale=args.cfg, sampler=args.sampler, hooks=tuple(hooks), noise=noise,
        )

    # Save the final latent for golden comparison.
    latent_path = out_path.with_suffix(".latent.pt")
    torch.save(latent.detach().cpu(), latent_path)

    with _phase("decode"):
        images = gen.decode(latent)

    _save_png(images[0], out_path)
    _report(latent, images, out_path, latent_path, is_cuda)
    return 0


def _save_png(image_hwc, path: Path) -> None:
    try:
        from PIL import Image
    except ImportError:
        print("[warn] PIL not available; skipping PNG (latent .pt still saved)", flush=True)
        return
    Image.fromarray(image_hwc).save(path)
    print(f"[out] image -> {path}", flush=True)


def _report(latent, images, out_path, latent_path, is_cuda) -> None:
    import numpy as np
    lat_finite = bool(torch.isfinite(latent).all())
    px = images.astype("float32")
    print("\n=== validation ===", flush=True)
    print(f"  latent: shape={tuple(latent.shape)} finite={lat_finite} "
          f"range=[{latent.float().min():.3f}, {latent.float().max():.3f}]", flush=True)
    print(f"  pixels: shape={images.shape} dtype={images.dtype} "
          f"range=[{int(px.min())}, {int(px.max())}] mean={px.mean():.1f} std={px.std():.1f}", flush=True)
    # plausible dynamic range: not all-zeros / not fully saturated flat.
    plausible = px.std() > 2.0 and not (px.min() == px.max())
    print(f"  plausible dynamic range: {plausible}", flush=True)
    print(f"  saved latent -> {latent_path}", flush=True)
    if is_cuda:
        peak = torch.cuda.max_memory_allocated() / (1024 ** 3)
        print(f"  peak VRAM: {peak:.2f} GB", flush=True)
    if not (lat_finite and plausible):
        print("  [FAIL] non-finite or degenerate output", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
