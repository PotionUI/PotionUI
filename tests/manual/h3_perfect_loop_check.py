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
    "GPU check for MiniMax-H3's perfect loop. Renders one loop through the real model loader, prompt "
    "encoder and generator pipes (or takes an existing loop with --video), then writes the loop, the loop "
    "three times in a row, a contact sheet of the frames around the seam, and the seam metric: the mean "
    "absolute luma difference between neighbouring frames around the whole period, the wrap included. PASS "
    "when no step is above 2.5x the median and 1.8x the steps four frames away (the VAE's own cadence), which "
    "would be a jump or a reset (with the earlier frame it jumps back to), and "
    "motion never drops below 0.35x the median for two steps (a stop and restart) nor below 0.5x on average in "
    "the half second either side of the wrap (an ease into or out of the seam), and the view never zooms "
    "more than 1 percent against the first frame."
)
PRESET = ROOT / "content/presets/marketplace/MiniMax-H3/preset.yml"
SEAM_TILES = 8


def loop_document(what_moves: str, *, picture: bool, lively: bool, sound: bool) -> str:
    scene = "The scene of <Picture 1>, filmed" if picture else "Filmed"
    pace = "Steady, clearly visible movement: " if lively else "Only slight, slow movement: "
    soundscape = "Quiet ambient sound that fits the scene, steady from start to end." if sound else "N/A"
    return (
        f"integrated_multimodal_description: [Shot 1] {scene} from a fixed camera that never moves, as one "
        f"continuous, seamless looping shot with no cuts. {pace}{what_moves.strip()} The motion is continuous and repeats naturally, "
        f"and everything looks the same at the end as at the start.\n"
        f"overall_soundscape: {soundscape}\n"
        f"non_diegetic_music: N/A"
    )


def luma(frames: np.ndarray) -> np.ndarray:
    rgb = frames.astype(np.float64)
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


SPIKE_RATIO = 2.5
CADENCE = 4
CADENCE_RATIO = 1.8
STOP_RATIO = 0.35
RESET_GAP = 12
SEAM_WINDOW = 12
SEAM_MOTION_MIN = 0.5


def still_spans(slow: np.ndarray) -> list[dict]:
    count = len(slow)
    if slow.all():
        return [{"start": 0, "frames": count}]
    origin = int(np.flatnonzero(~slow)[0])
    runs: list[list[int]] = []
    for offset in range(1, count + 1):
        index = (origin + offset) % count
        if slow[index] and not slow[index - 1]:
            runs.append([offset, offset])
        elif slow[index]:
            runs[-1][1] = offset
    merged: list[list[int]] = []
    for first, last in (run for run in runs if run[1] > run[0]):
        if merged and first - merged[-1][1] <= CADENCE:
            merged[-1][1] = last
        else:
            merged.append([first, last])
    return [{"start": int((origin + first) % count), "frames": last - first + 2} for first, last in merged]


def seam_metric(frames: np.ndarray) -> dict:
    y = luma(frames)
    steps = np.abs(np.diff(y, axis=0)).mean(axis=(1, 2))
    seam = float(np.abs(y[0] - y[-1]).mean())
    cycle = np.append(steps, seam)
    count = len(cycle)
    median = float(np.median(cycle))
    jumps = []
    for index in range(count):
        same_phase = max(cycle[index - CADENCE], cycle[(index + CADENCE) % count])
        if cycle[index] <= SPIKE_RATIO * median or cycle[index] <= CADENCE_RATIO * same_phase:
            continue
        after = y[(index + 1) % len(y)]
        earlier = np.abs(y[: max(0, index - RESET_GAP)] - after).mean(axis=(1, 2))
        back_to = int(np.argmin(earlier)) if earlier.size and earlier.min() < cycle[index] else None
        jumps.append({
            "frames": [int(index), int((index + 1) % len(y))], "step": float(cycle[index]),
            "back_to": back_to, "at_wrap": index == count - 1,
        })
    stops = still_spans(cycle < STOP_RATIO * median)
    scale = median if median else 1.0
    before_seam = float(cycle[-1 - SEAM_WINDOW: -1].mean() / scale)
    after_seam = float(cycle[:SEAM_WINDOW].mean() / scale)
    seam_motion_ok = min(before_seam, after_seam) >= SEAM_MOTION_MIN
    largest = int(np.argmax(steps))
    return {
        "frames": int(frames.shape[0]),
        "seam_step": seam,
        "mean_step": float(steps.mean()),
        "median_step": median,
        "largest_step": float(steps[largest]),
        "largest_step_at": [largest, largest + 1],
        "seam_over_median": seam / median if median else float("inf"),
        "jumps": jumps,
        "stops": stops,
        "before_seam_over_median": before_seam,
        "after_seam_over_median": after_seam,
        "seam_motion_ok": seam_motion_ok,
        "pass": not jumps and not stops and seam_motion_ok,
    }


ZOOM_LIMIT = 0.01
ZOOM_STEP = 12


def zoom_drift(frames: np.ndarray, *, size: tuple[int, int] = (96, 168)) -> dict:
    def small(frame: np.ndarray) -> np.ndarray:
        return np.asarray(Image.fromarray(frame).convert("L").resize(size), dtype=np.float64)

    def zoomed(frame: np.ndarray, scale: float) -> np.ndarray:
        image = Image.fromarray(frame)
        width, height = image.size
        grown = image.resize((round(width * scale), round(height * scale)))
        left, top = (grown.size[0] - width) // 2, (grown.size[1] - height) // 2
        return small(np.asarray(grown.crop((left, top, left + width, top + height))))

    scales = np.round(np.arange(0.94, 1.0601, 0.005), 3)
    references = {float(scale): zoomed(frames[0], float(scale)) for scale in scales}
    found = []
    for index in range(0, len(frames), ZOOM_STEP):
        target = small(frames[index])
        best = min(references, key=lambda scale: np.abs(references[scale] - target).mean())
        found.append((index, best))
    worst = max(found, key=lambda item: abs(item[1] - 1.0))
    return {"zoom_by_frame": found, "worst_zoom": worst[1], "worst_zoom_at": worst[0],
            "zoom_ok": abs(worst[1] - 1.0) <= ZOOM_LIMIT}


def contact_sheet(frames: np.ndarray, path: Path, *, tiles: int = SEAM_TILES, width: int = 320) -> None:
    picked = list(range(len(frames) - tiles, len(frames))) + list(range(tiles))
    height = round(width * frames.shape[1] / frames.shape[2])
    sheet = Image.new("RGB", (width * tiles, (height + 24) * 2), "black")
    draw = ImageDraw.Draw(sheet)
    for slot, index in enumerate(picked):
        x, y = (slot % tiles) * width, (slot // tiles) * (height + 24)
        sheet.paste(Image.fromarray(frames[index]).resize((width, height)), (x, y + 24))
        label = f"frame {index}" + (" (last)" if index == len(frames) - 1 else " (first)" if index == 0 else "")
        draw.text((x + 6, y + 6), label, fill="white")
    sheet.save(path)


def read_frames(path: Path) -> tuple[np.ndarray, float]:
    from src.pipelines.pipes._shared.media.video_read import read_video_frames

    frames, fps = read_video_frames(path)
    return np.stack([np.asarray(frame.convert("RGB")) for frame in frames]), fps


def render_loop(args) -> Path:
    import yaml

    from src.pipelines.contracts import PipeInput
    from src.pipelines.pipes.generator.video_minimax_h3.main import GeneratorMinimaxH3Pipe
    from src.pipelines.pipes.model_loader.minimax_h3.main import ModelLoaderMinimaxH3Pipe
    from src.pipelines.pipes.prompt_encoder.main import PromptEncoderPipe

    def sink(*_args, **_kwargs):
        return None

    def file(path):
        return {"file_path": str(path), "name": Path(path).name}

    profile = yaml.safe_load(PRESET.read_text(encoding="utf-8"))["speed_profiles"][args.profile]
    extra = profile.get("extra") or {}
    runtime_loras = []
    if profile.get("sampler") == "lcm":
        if not args.flex_adapter:
            raise SystemExit("the flex profile needs --flex-adapter")
        runtime_loras = [{"file_path": str(args.flex_adapter), "weight": 1.0, "audio": True}]

    loader = ModelLoaderMinimaxH3Pipe({
        **ModelLoaderMinimaxH3Pipe.get_default_config(), "model": file(args.dit),
        "text_encoder": file(args.text_encoder), "video_vae": file(args.video_vae),
        "audio_vae": file(args.audio_vae), "loras": [],
    })
    models = loader.process(PipeInput(input={}), sink).output

    picture = [Image.open(args.picture).convert("RGB")] if args.picture else []
    document = loop_document(args.prompt, picture=bool(picture), lively=args.lively, sound=args.sound)
    encoder = PromptEncoderPipe({
        **PromptEncoderPipe.get_default_config(), "p_prompt": {"input": document, "output": document},
        "pairs": [{"positive": document, "negative": ""}], "quantity": 1, "guidance_scale": 1.0,
    })
    encoded = encoder.process(PipeInput(input={"text_encoder": models["text_encoder"], "image": picture}), sink)

    generator = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(), "mode": "video", "loop": True,
        "keyframe_anchors": ["first"] if picture else [], "resolution": args.screen,
        "frames": round(args.seconds * 24), "steps": int(profile.get("steps", 24)), "quantity": 1,
        "preview": False, "audio_source": "generate" if args.sound else "none", "decode": True,
        "sampler": profile.get("sampler", "euler"), "scheduler": profile.get("schedule", "simple"),
        "manual_sigmas": extra.get("manual_sigmas", ""), "manual_audio_sigmas": extra.get("manual_audio_sigmas", ""),
        "step_cache_threshold": float(extra.get("step_cache_threshold", 0.0)), "runtime_loras": runtime_loras,
        "device": args.device,
    })
    result = generator.process(PipeInput(input={
        "model": models["model"], "conditioning": encoded.output["conditioning"], "seed": [args.seed],
        "image": picture,
    }), sink)
    rendered = Path(result.output["video"][0])
    target = args.out / "loop.mp4"
    target.write_bytes(rendered.read_bytes())
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--out", type=Path, required=True, help="directory for loop.mp4, loop-x3.mp4, seam-sheet.png, seam.json")
    parser.add_argument("--video", type=Path, help="measure this existing loop instead of rendering one")
    parser.add_argument("--picture", type=Path, help="start picture (optional)")
    parser.add_argument("--prompt", default="the water ripples and the grass sways in a light breeze", help="what moves")
    parser.add_argument("--seconds", type=float, default=6.0)
    parser.add_argument("--screen", default="auto", help="auto or WxH on the 32-pixel grid, e.g. 1024x576")
    parser.add_argument("--profile", default="quality", choices=["quality", "turbo", "flex"])
    parser.add_argument("--lively", action="store_true")
    parser.add_argument("--sound", action="store_true")
    parser.add_argument("--seed", type=int, default=5101)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--dit", type=Path)
    parser.add_argument("--text-encoder", type=Path)
    parser.add_argument("--video-vae", type=Path)
    parser.add_argument("--audio-vae", type=Path)
    parser.add_argument("--flex-adapter", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    if args.video is None:
        missing = [name for name in ("dit", "text_encoder", "video_vae", "audio_vae") if getattr(args, name) is None]
        if missing:
            parser.error("rendering needs --" + ", --".join(name.replace("_", "-") for name in missing))
        source = render_loop(args)
    else:
        source = args.video

    from src.pipelines.pipes._shared.media.video_encode import encode_frames_to_mp4

    frames, fps = read_frames(source)
    if source != args.out / "loop.mp4":
        encode_frames_to_mp4(frames, args.out / "loop.mp4", fps=fps)
    encode_frames_to_mp4(np.concatenate([frames, frames, frames]), args.out / "loop-x3.mp4", fps=fps)
    contact_sheet(frames, args.out / "seam-sheet.png")
    report = seam_metric(frames)
    report.update(zoom_drift(frames))
    report["pass"] = report["pass"] and report["zoom_ok"]
    (args.out / "seam.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"frames {report['frames']} at {fps:g} fps")
    print(f"seam step (last to first)   {report['seam_step']:.2f}")
    print(f"neighbour step mean/median  {report['mean_step']:.2f} / {report['median_step']:.2f}")
    print(f"largest step inside         {report['largest_step']:.2f} at frames {report['largest_step_at']}")
    print(f"seam / median               {report['seam_over_median']:.2f}")
    for jump in report["jumps"]:
        back = f", the picture jumps back to frame {jump['back_to']}" if jump["back_to"] is not None else ""
        where = " (the wrap)" if jump["at_wrap"] else ""
        print(f"jump at frames {jump['frames'][0]}-{jump['frames'][1]}{where}: step {jump['step']:.2f}{back}")
    print(f"motion in the last / first {SEAM_WINDOW} steps around the wrap, over the median  "
          f"{report['before_seam_over_median']:.2f} / {report['after_seam_over_median']:.2f} "
          f"(at least {SEAM_MOTION_MIN})")
    for stop in report["stops"]:
        print(f"motion stops at frame {stop['start']} and the picture holds for {stop['frames']} frames "
              f"({stop['frames'] / fps:.2f} s)")
    print(f"zoom against frame 0          worst {report['worst_zoom']:.3f} at frame {report['worst_zoom_at']} "
          f"(limit 1 +/- {ZOOM_LIMIT})")
    print(f"{'PASS' if report['pass'] else 'FAIL'}: no step of the period, the wrap included, jumps above "
          f"{SPIKE_RATIO}x the median and {CADENCE_RATIO}x the steps {CADENCE} frames away, and motion never "
          f"drops below {STOP_RATIO}x the median for two steps nor below {SEAM_MOTION_MIN}x on average in the "
          f"{SEAM_WINDOW} steps either side of the wrap")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
