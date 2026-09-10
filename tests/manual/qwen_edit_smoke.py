#!/usr/bin/env python
"""Manual GPU harness for Qwen-Image-Edit (2511), native engine, edit mode.

Runs ONE edit-mode generation with 1..N reference images the same way the
shipped preset does (content/presets/marketplace/QwenImage/modes/edit/pipeline.yml):

    model_loader/qwen (vision: true) -> media_loader -> prompt_encoder (+image)
      -> generator/qwen (mode=edit)

Every ``--source`` image goes to BOTH the vision-grounded text encode (the
Qwen2.5-VL text encoder's vision tower, loaded via ``vision=True`` — see
``src/pipelines/pipes/model_loader/qwen/main.py:152-153``) AND the DiT's
``ref_latents`` (the checkpoint's in-context edit path — see
``src/pipelines/pipes/generator/qwen/main.py``'s ``GeneratorQwenPipe.maybe_edit``,
lines 184-260), in upload order — same idiom as ``qwen_clip.py``'s
``QwenClipTextEncoder.forwards_full_image_batch``. ``--source`` item 0 is the
edit target/sizing anchor: the OUTPUT canvas (and hence ``--width``/``--height``)
is derived from it alone, at a fixed ~1-megapixel area target
(``GeneratorQwenPipe.EDIT_AREA_TARGET`` / ``_edit_target_size``, lines 78/268-276)
— NOT from ``--width``/``--height``, which edit mode ignores (kept here only for
CLI parity with the other harnesses; see the printed note at runtime).

Usage
-----
Single reference:
    python tests/manual/qwen_edit_smoke.py \\
        --dit models/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors \\
        --te models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors \\
        --vae models/vae/qwen_image_vae.safetensors \\
        --source subject.png \\
        --prompt "make the sky a dramatic sunset" \\
        --steps 20 --cfg 4.0 --seed 42 --device cuda:0 \\
        --out /tmp/qwen_edit.png

Two references (order preserved -- source[0] is the sizing anchor):
    python tests/manual/qwen_edit_smoke.py \\
        --dit models/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors \\
        --te models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors \\
        --vae models/vae/qwen_image_vae.safetensors \\
        --source scene.png --source character.png \\
        --prompt "place the character from the second image into the scene" \\
        --steps 20 --cfg 4.0 --seed 42 --device cuda:0 \\
        --out /tmp/qwen_edit_multi.png
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
import torch  # noqa: E402
from PIL import Image  # noqa: E402

from src.platform.runtime.native.engine import (  # noqa: E402
    Conditioning,
    NativeEngineLoader,
    NativeGenerator,
)
from src.platform.runtime.native.memory.device_plan import DevicePlan  # noqa: E402
from tests.manual.native_smoke import _phase, _report, _save_png, _StdoutProgress  # noqa: E402

logger = logging.getLogger("qwen_edit_smoke")

# generator/qwen/main.py:78 -- ComfyUI TextEncodeQwenImageEdit's fixed resize
# target for the VAE-encoded reference latent (aspect preserved, up OR down).
EDIT_AREA_TARGET = 1024 * 1024


def _to_image_tensor(image: Image.Image) -> torch.Tensor:
    """PIL -> ``[H, W, 3]`` float32 in ``[0, 1]`` -- exact copy of
    ``src/pipelines/pipes/model_loader/qwen/qwen_clip.py``'s ``_to_image_tensor``
    (lines 57-64), the conversion the real ``QwenClipTextEncoder`` applies before
    handing images to ``Qwen25VLTextEncoder.encode``."""
    arr = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(arr)


def _edit_target_size(gen: NativeGenerator, src_size: tuple[int, int]) -> tuple[int, int]:
    """Exact copy of ``GeneratorQwenPipe._edit_target_size``
    (generator/qwen/main.py:268-276): the source's own aspect scaled to hit
    ``EDIT_AREA_TARGET`` exactly, snapped to the DiT's patch granularity."""
    sw, sh = src_size
    scale = (EDIT_AREA_TARGET / max(1, sw * sh)) ** 0.5
    return gen.snap_resolution(max(1, round(sw * scale)), max(1, round(sh * scale)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dit", required=True, help="Qwen-Image-Edit MMDiT checkpoint")
    ap.add_argument("--te", required=True, help="Qwen2.5-VL-7B text encoder (single TE, no CLIP-L)")
    ap.add_argument("--vae", required=True, help="Qwen-Image (Wan-2.1 causal-3D) VAE")
    ap.add_argument("--source", action="append", default=[],
                     help="repeatable reference image, upload order preserved (item 0 = edit "
                          "target/sizing anchor); at least one required")
    ap.add_argument("--prompt", default="")
    ap.add_argument("--negative", default="")
    ap.add_argument("--steps", type=int, default=20, help="edit preset's 'balanced'/'custom' default")
    ap.add_argument("--cfg", "--guidance", dest="cfg", type=float, default=4.0,
                     help="true_cfg_scale; edit preset's 'balanced'/'custom' default")
    ap.add_argument("--sampler", default="euler")
    ap.add_argument("--width", type=int, default=1024,
                     help="UNUSED by edit mode (kept for CLI parity) -- see module docstring")
    ap.add_argument("--height", type=int, default=1024,
                     help="UNUSED by edit mode (kept for CLI parity) -- see module docstring")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--tier-vram", type=float, default=None, help="override detected VRAM budget (GB)")
    ap.add_argument("--out", default="out.png")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not args.source:
        ap.error("provide at least one --source reference image")

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

    # model_loader/qwen/main.py:152-153 -- `vision: true` keeps+loads the
    # checkpoint's vision tower so the TE can see the source image(s).
    with _phase("load-te (vision=True)"):
        te = loader.load(args.te, "text_encoder", vision=True).module
    print(f"  TE vision tower loaded: {te._has_vision}", flush=True)

    with _phase("load-vae"):
        vae = loader.load(args.vae, "vae")

    device_plan = DevicePlan(device, device, device)
    gen = NativeGenerator(dit, te, vae, device_plan, vram_gb=args.tier_vram)

    # media_loader -- PIL images, upload order preserved (pipeline.yml's
    # `@loop` over `form.source_image`).
    images = [Image.open(p).convert("RGB") for p in args.source]
    print(f"  sources: {len(images)} image(s): {args.source}", flush=True)

    # prompt_encoder/main.py:_do_cfg (405-416) -- Qwen-Image's true CFG: the
    # negative pass is only encoded when guidance_scale > 1.0.
    do_cfg = args.cfg > 1.0

    # qwen_clip.py:_encode_fn_and_key (~90-168) -- image-conditioned encode:
    # BOTH cond and uncond are vision-grounded on the SAME full reference set
    # (QwenClipTextEncoder.forwards_full_image_batch), never per-index.
    image_tensors = [_to_image_tensor(img) for img in images]
    te.to(device)
    with _phase("encode-prompt (vision-grounded)"):
        cond = dict(te.encode([args.prompt], images=image_tensors))
        uncond = dict(te.encode([args.negative], images=image_tensors)) if do_cfg else None
    print(f"  cond keys (pre ref_latents): {sorted(cond)}", flush=True)
    if uncond is not None:
        print(f"  uncond keys (pre ref_latents): {sorted(uncond)}", flush=True)

    # generator/qwen/main.py:maybe_edit (184-260) -- images[0] is the primary
    # (drives output size); every image, including it, is independently
    # resized to its OWN area-target aspect and VAE-encoded into ref_latents.
    primary = images[0]
    width, height = _edit_target_size(gen, primary.size)
    print(f"  requested --width/--height ({args.width}x{args.height}) are IGNORED by edit mode -- "
          f"size is derived from source[0]'s own aspect at a "
          f"{EDIT_AREA_TARGET / 1_000_000:.2f}MP area target: computed {width}x{height}", flush=True)

    ref_latents = []
    with _phase("vae-encode refs"):
        for i, image in enumerate(images):
            src = primary if i == 0 else image
            target = (width, height) if i == 0 else _edit_target_size(gen, src.size)
            if target != src.size:
                src = src.resize(target, Image.LANCZOS)
            ref_latents.append(gen.encode_image(np.asarray(src)))
    print(f"  ref_latents shapes: {[tuple(r.shape) for r in ref_latents]}", flush=True)

    cond["ref_latents"] = ref_latents
    if uncond is not None:
        uncond["ref_latents"] = ref_latents
    conditioning = Conditioning(cond, uncond)
    print(f"  cond keys (final, pre-sample): {sorted(cond)}", flush=True)

    latents_shape = gen.latent_shape_for(width, height)
    print(f"  latent shape: {latents_shape}", flush=True)

    out_path = Path(args.out)

    with _phase("sample"):
        latent = gen.sample(
            conditioning, latents_shape, steps=args.steps, seed=args.seed,
            cfg_scale=args.cfg, sampler=args.sampler, hooks=(_StdoutProgress(),),
        )

    latent_path = out_path.with_suffix(".latent.pt")
    torch.save(latent.detach().cpu(), latent_path)

    with _phase("decode"):
        pixels = gen.decode(latent)

    _save_png(pixels[0], out_path)
    _report(latent, pixels, out_path, latent_path, is_cuda)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
