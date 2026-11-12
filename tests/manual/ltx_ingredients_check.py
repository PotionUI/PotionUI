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
    "--no-hold to compare the repeated still sheet against a single-frame reference."
)
TILES = 8


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


def mode_pipes(args, labels: list[str | None], prompt: str):
    from unittest.mock import Mock

    from src.features.forms.binding import bind_form
    from src.features.presets import PresetTemplateLoader
    from src.features.presets.processor import PresetProcessor
    from src.platform.templating.processor import TemplateProcessor

    loader = PresetTemplateLoader([str(ROOT / "content/presets")])
    loader.load_presets()
    template = next(p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/LTX-2.5"))
    raw = {
        "quantity": 1, "seed": args.seed, "resolution": args.size, "duration": round(args.seconds),
        "speed_profile": args.profile, "effect_strength": args.strength, "manual_sigmas": "",
        "model": str(args.dit), "text_encoder": str(args.text_encoder), "video_vae": str(args.video_vae),
        "effect_lora": str(args.lora),
    }
    if args.sheet:
        raw["reference_sheet"] = str(args.sheet)
    else:
        raw["pictures"] = [
            {"path": str(path), **({"label": label} if label else {})} for path, label in zip(args.picture, labels)
        ]
    bound = bind_form(template, "ingredients", form_name=None, raw_form_data=raw, user_id=None, storage_dir=None)
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    pipes = processor.process(template, {
        "prompts": [{"positive": prompt, "negative": ""}], "mode": "ingredients", "form_data": dict(bound.values),
    })
    return {pipe.get("id") or pipe["name"]: pipe["config"] for pipe in pipes}


def render(args, sheet_input: list[Image.Image], labels: list[str | None], prompt: str):
    from src.pipelines.contracts import PipeInput
    from src.pipelines.pipes.generator.video_ltx.main import GeneratorLtxVideoPipe
    from src.pipelines.pipes.image_sheet.main import ImageSheetPipe
    from src.pipelines.pipes.model_loader.ltx.main import ModelLoaderLtxPipe
    from src.pipelines.pipes.prompt_encoder.main import PromptEncoderPipe

    def sink(*_args, **_kwargs):
        return None

    configs = mode_pipes(args, labels, prompt)
    print(configs["prompt_encoder"]["p_prompt"]["output"])

    sheet = ImageSheetPipe({**ImageSheetPipe.get_default_config(), **configs["image_sheet"]}).process(
        PipeInput(input={"image": sheet_input}), sink).output["image"][0]
    sheet.save(args.out / "reference-sheet.png")

    models = ModelLoaderLtxPipe({**ModelLoaderLtxPipe.get_default_config(), **configs["model_loader/ltx"]}).process(
        PipeInput(input={}), sink).output
    encoded = PromptEncoderPipe({**PromptEncoderPipe.get_default_config(), **configs["prompt_encoder"]}).process(
        PipeInput(input={"text_encoder": models["text_encoder"]}), sink)

    generator_config = {**GeneratorLtxVideoPipe.get_default_config(), **configs["generator"], "device": args.device}
    if not args.hold:
        generator_config["media_placements"] = [
            {key: value for key, value in placement.items() if key != "hold"}
            for placement in generator_config["media_placements"]
        ]
    result = GeneratorLtxVideoPipe(generator_config).process(PipeInput(input={
        "model": models["model"], "conditioning": encoded.output["conditioning"],
        "seed": [args.seed], "image": [sheet],
    }), sink)
    target = args.out / "clip.mp4"
    target.write_bytes(Path(result.output["video"][0]).read_bytes())
    return sheet, target, configs


def main() -> int:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--out", type=Path, required=True, help="directory for clip.mp4, reference-sheet.png, contact-sheet.png, run.json")
    parser.add_argument("--picture", type=Path, action="append", default=[], help="a character or prop picture; repeat up to 6 times")
    parser.add_argument("--name", action="append", default=[], help="the optional label of the matching --picture")
    parser.add_argument("--sheet", type=Path, help="a ready-made reference sheet, instead of pictures")
    parser.add_argument("--prompt", required=True, help="the prompt, as typed in the app")
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
    labels = [*args.name, *[None] * len(args.picture)][: len(args.picture)]

    sheet, clip, configs = render(args, images, labels, args.prompt)

    from src.pipelines.pipes._shared.media.video_read import read_video_frames

    frames, fps = read_video_frames(clip)
    contact_sheet(sheet, pick_frames(frames, TILES), args.out / "contact-sheet.png")
    (args.out / "run.json").write_text(json.dumps({
        "prompt": configs["prompt_encoder"]["p_prompt"]["output"], "frames": len(frames), "fps": fps, "size": args.size, "profile": args.profile,
        "strength": args.strength, "hold": args.hold, "seed": args.seed,
    }, indent=2), encoding="utf-8")
    print(f"{len(frames)} frames at {fps:g} fps")
    print(f"look at {args.out / 'contact-sheet.png'}: the characters and props of the sheet should show up in the frames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
