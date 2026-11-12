from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DESCRIPTION = (
    "GPU check for LTX-2.5 Ingredients. Lays the pictures out as one reference sheet, renders one clip through "
    "the real model loader, prompt encoder, sheet and generator pipes, and writes the clip, the reference sheet "
    "and a contact sheet with the reference sheet beside frames of the result, so a person can judge whether "
    "the characters and props carried over. Pass --sheet instead of pictures to try a ready-made sheet, and "
    "--hold-frames/--no-hold to compare the repeated still sheet against a single-frame reference."
)
PRESET = ROOT / "content/presets/marketplace/LTX-2.5/preset.yml"
NEGATIVE = "worst quality, inconsistent motion, blurry, jittery, distorted"
TILES = 8
NUMBERS = ["", "one", "two", "three", "four", "five", "six"]


def sheet_text(names: list[str], count: int) -> str:
    if count and len(names) == count:
        joined = names[0] if count == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        return f"{NUMBERS[count]} {'panel' if count == 1 else 'panels'} showing {joined}"
    if count == 1:
        return "one panel showing a character or a prop"
    if count:
        return f"{NUMBERS[count]} panels, each showing a character or a prop"
    return "a sheet of panels, each showing a character or a prop"


def caption(described: str, action: str) -> str:
    action = action.strip()
    if action[:16].lower() == "reference sheet:":
        return action
    return f"Reference sheet: {described}. Generated video: {action}"


def frame_count(seconds: float) -> int:
    return round(seconds * 3) * 8 + 1


def contact_sheet(sheet: Image.Image, frames: list[Image.Image], path: Path, *, tile: int = 320) -> None:
    height = round(tile * frames[0].height / frames[0].width)
    columns = 4
    rows = (len(frames) + columns - 1) // columns
    side = max(tile * 2, round(sheet.width * (height * rows) / sheet.height))
    reference = sheet.resize((side, round(side * sheet.height / sheet.width)))
    total = max(reference.height + 24, rows * (height + 24))
    canvas = Image.new("RGB", (side + columns * tile, total), "black")
    draw = ImageDraw.Draw(canvas)
    canvas.paste(reference, (0, 24))
    draw.text((6, 6), "reference sheet", fill="white")
    for slot, frame in enumerate(frames):
        x, y = side + (slot % columns) * tile, (slot // columns) * (height + 24)
        canvas.paste(frame.resize((tile, height)), (x, y + 24))
        draw.text((x + 6, y + 6), f"frame {frame.info.get('index', slot)}", fill="white")
    canvas.save(path)


def pick_frames(frames: list[Image.Image], count: int) -> list[Image.Image]:
    indices = np.linspace(0, len(frames) - 1, count).round().astype(int)
    picked = []
    for index in indices:
        frame = frames[int(index)].copy()
        frame.info["index"] = int(index)
        picked.append(frame)
    return picked


def render(args, sheet_input: list[Image.Image], panels: int, prompt: str):
    import yaml

    from src.pipelines.contracts import PipeInput
    from src.pipelines.pipes.generator.video_ltx.main import GeneratorLtxVideoPipe
    from src.pipelines.pipes.image_sheet.main import ImageSheetPipe
    from src.pipelines.pipes.model_loader.ltx.main import ModelLoaderLtxPipe
    from src.pipelines.pipes.prompt_encoder.main import PromptEncoderPipe

    def sink(*_args, **_kwargs):
        return None

    def file(path):
        return {"file_path": str(path), "name": Path(path).name}

    preset = yaml.safe_load(PRESET.read_text(encoding="utf-8"))
    profile = preset["speed_profiles"][args.profile]
    recipe = preset["vars"]["distilled_sigma_recipe"] if args.profile == "distilled" else ""

    sheet = ImageSheetPipe({
        **ImageSheetPipe.get_default_config(), "resolution": args.size, "pictures": panels,
    }).process(PipeInput(input={"image": sheet_input}), sink).output["image"][0]
    sheet.save(args.out / "reference-sheet.png")

    loader = ModelLoaderLtxPipe({
        **ModelLoaderLtxPipe.get_default_config(), "model": file(args.dit), "text_encoder": file(args.text_encoder),
        "vae": file(args.video_vae), "loras": [],
    })
    models = loader.process(PipeInput(input={}), sink).output

    cfg = float(profile["guidance"])
    encoder = PromptEncoderPipe({
        **PromptEncoderPipe.get_default_config(), "p_prompt": {"input": prompt, "output": prompt},
        "n_prompt": {"input": NEGATIVE, "output": NEGATIVE},
        "pairs": [{"positive": prompt, "negative": NEGATIVE}], "quantity": 1,
        "guidance_scale": cfg, "negative_applied": cfg > 1,
    })
    encoded = encoder.process(PipeInput(input={"text_encoder": models["text_encoder"]}), sink)

    placement = {"source": "image", "index": 0, "frame": "first", "strength": 1.0, "role": "reference"}
    if args.hold:
        placement["hold"] = True
    generator = GeneratorLtxVideoPipe({
        **GeneratorLtxVideoPipe.get_default_config(), "quantity": 1, "decode": True, "audio": False,
        "resolution": args.size, "frames": frame_count(args.seconds), "fps": 24.0, "steps": int(profile["steps"]),
        "cfg": cfg, "sampler": profile["sampler"], "schedule": "ltx_dynamic", "manual_sigmas": recipe,
        "media_placements": [placement],
        "scoped_loras": [{"file_path": str(args.lora), "weight": args.strength}], "device": args.device,
    })
    result = generator.process(PipeInput(input={
        "model": models["model"], "conditioning": encoded.output["conditioning"], "seed": [args.seed],
        "image": [sheet],
    }), sink)
    target = args.out / "clip.mp4"
    target.write_bytes(Path(result.output["video"][0]).read_bytes())
    return sheet, target


def main() -> int:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--out", type=Path, required=True, help="directory for clip.mp4, reference-sheet.png, contact-sheet.png, run.json")
    parser.add_argument("--picture", type=Path, action="append", default=[], help="a character or prop picture; repeat up to 6 times")
    parser.add_argument("--name", action="append", default=[], help="a name for the matching --picture; repeat to name them all")
    parser.add_argument("--sheet", type=Path, help="a ready-made reference sheet, instead of pictures")
    parser.add_argument("--prompt", required=True, help="what happens")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--size", default="768x448")
    parser.add_argument("--profile", default="distilled", choices=["distilled", "balanced", "quality"])
    parser.add_argument("--strength", type=float, default=1.0)
    parser.add_argument("--hold", action=argparse.BooleanOptionalAction, default=True,
                        help="repeat the sheet for every frame (default) or give it as one frame")
    parser.add_argument("--seed", type=int, default=5101)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--dit", type=Path, required=True)
    parser.add_argument("--text-encoder", type=Path, required=True)
    parser.add_argument("--video-vae", type=Path, required=True)
    parser.add_argument("--lora", type=Path, required=True, help="ltx-2.5-22b-ic-lora-ingredients-0.9.safetensors")
    args = parser.parse_args()
    if bool(args.picture) == bool(args.sheet):
        parser.error("give pictures or one --sheet, not both and not neither")
    args.out.mkdir(parents=True, exist_ok=True)

    images = [Image.open(path).convert("RGB") for path in (args.picture or [args.sheet])]
    panels = len(args.picture)
    described = sheet_text(args.name, panels)
    prompt = caption(described, args.prompt)
    print(prompt)

    sheet, clip = render(args, images, panels, prompt)

    from src.pipelines.pipes._shared.media.video_read import read_video_frames

    frames, fps = read_video_frames(clip)
    contact_sheet(sheet, pick_frames(frames, TILES), args.out / "contact-sheet.png")
    (args.out / "run.json").write_text(json.dumps({
        "prompt": prompt, "frames": len(frames), "fps": fps, "size": args.size, "profile": args.profile,
        "strength": args.strength, "hold": args.hold, "seed": args.seed,
    }, indent=2), encoding="utf-8")
    print(f"{len(frames)} frames at {fps:g} fps")
    print(f"look at {args.out / 'contact-sheet.png'}: the characters and props of the sheet should show up in the frames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
