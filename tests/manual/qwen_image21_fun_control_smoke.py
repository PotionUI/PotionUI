from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DESCRIPTION = (
    "GPU smoke test for the Qwen-Image-2.1 Fun ControlNet Union on the native engine. "
    "Loads the DiT with the control patch, checks that strength 0 reproduces the uncontrolled "
    "latent bit for bit, then renders a controlled image and, when given, an inpaint."
)


def _with_control(conditioning, context: torch.Tensor | None, scale: float):
    from src.platform.runtime.native.engine import Conditioning

    if context is None:
        return conditioning
    extra = {"control_context": context, "control_context_scale": scale}
    uncond = {**conditioning.uncond, **extra} if conditioning.uncond is not None else None
    return Conditioning(cond={**conditioning.cond, **extra}, uncond=uncond)


def _sample(gen, conditioning, shape, args, steps=None):
    started = time.perf_counter()
    noise = torch.randn(shape, generator=torch.Generator().manual_seed(args.seed))
    latent = gen.sample(conditioning, shape, steps=steps or args.steps, seed=args.seed, cfg_scale=args.cfg,
                        sampler="euler", noise=noise)
    print(f"  sampled in {time.perf_counter() - started:.1f}s", flush=True)
    return latent


def _save(gen, latent, path: Path) -> None:
    Image.fromarray(gen.decode(latent)[0]).save(path)
    print(f"  wrote {path}", flush=True)


def main() -> int:
    from src.pipelines.pipes.generator.qwen_image21.control import control_canvas, encode_control_context
    from src.platform.runtime.native.engine import NativeEngineLoader, NativeGenerator
    from src.platform.runtime.native.memory.device_plan import DevicePlan

    ap = argparse.ArgumentParser(description=DESCRIPTION)
    ap.add_argument("--dit", required=True, help="qwen_image_2.1_bf16.safetensors")
    ap.add_argument("--te", required=True, help="qwen3vl_8b_bf16.safetensors")
    ap.add_argument("--vae", required=True, help="qwen_image_2.1_vae_bf16.safetensors")
    ap.add_argument("--control", required=True, help="Qwen-Image-2.1-Fun-Controlnet-Union.safetensors")
    ap.add_argument("--control-image", required=True, help="a ready-made control map (canny, depth, pose, ...)")
    ap.add_argument("--inpaint-image", default=None)
    ap.add_argument("--mask", default=None, help="white where to repaint")
    ap.add_argument("--prompt", default="a young woman in a white dress standing on a beach at noon, photo")
    ap.add_argument("--negative", default=" ")
    ap.add_argument("--strength", type=float, default=1.0)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=43)
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--out", default="fun_control")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    loader = NativeEngineLoader(device=args.device)
    dit = loader.load(args.dit, "diffusion_model", model_patch=args.control)
    print(f"  DiT + control ~{dit.estimated_vram_gb:.1f}GB quant={dit.quant_format}", flush=True)
    te = loader.load(args.te, "text_encoder").module
    vae = loader.load(args.vae, "vae")
    gen = NativeGenerator(dit, te, vae, DevicePlan(args.device, args.device, args.device))
    conditioning = gen.encode_prompt(args.prompt, args.negative)

    control_image = Image.open(args.control_image)
    width, height = control_canvas(gen, args.width, args.height, control_image)
    shape = gen.latent_shape_for(width, height)
    context = encode_control_context(gen, width, height, control_image, None, None)

    print("identity: strength 0 against no control (4 steps)", flush=True)
    plain = _sample(gen, conditioning, shape, args, steps=4)
    zero = _sample(gen, _with_control(conditioning, context, 0.0), shape, args, steps=4)
    identical = torch.equal(plain, zero)
    print(f"  bit-identical: {identical}", flush=True)

    print(f"control: strength {args.strength}, {width}x{height}", flush=True)
    latent = _sample(gen, _with_control(conditioning, context, args.strength), shape, args)
    _save(gen, latent, out / "control.png")
    _save(gen, _sample(gen, conditioning, shape, args), out / "no_control.png")

    if args.inpaint_image:
        source = Image.open(args.inpaint_image)
        mask = Image.open(args.mask) if args.mask else None
        width, height = control_canvas(gen, args.width, args.height, source)
        shape = gen.latent_shape_for(width, height)
        print(f"inpaint with the control image: {width}x{height}", flush=True)
        context = encode_control_context(gen, width, height, control_image, source, mask)
        latent = _sample(gen, _with_control(conditioning, context, args.strength), shape, args)
        _save(gen, latent, out / "inpaint.png")

    if torch.cuda.is_available():
        print(f"  peak VRAM {torch.cuda.max_memory_allocated() / 1024 ** 3:.1f}GB", flush=True)
    return 0 if identical else 1


if __name__ == "__main__":
    raise SystemExit(main())
