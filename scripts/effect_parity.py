from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import yaml

from src.features.media_tools.service import effect_block
from src.platform.media_tools.effects.loader import CORE_ROOT, EffectLoader
from src.platform.media_tools.effects.parity import (
    ParityCase,
    ParityError,
    case_params,
    fixture_frame,
    golden_name,
    over_tolerance,
    parity_cases,
    parity_media,
    parity_metric,
    read_golden,
    render_case,
    score_case,
    write_golden,
)
from src.platform.media_tools.effects.runtime import installed_filters, torch_installed
from src.platform.media_tools.effects.spec import Candidate, EffectSpec
from src.platform.media_tools.effects.tool import EffectTool, effect_tool_spec
from src.platform.media_tools.types import CORE_SOURCE
from src.platform.util.video_transcode import find_ffmpeg

CORE_GOLDEN = ROOT / "tests" / "fixtures" / "effects" / "golden"
PLUGIN_TREES = (ROOT / "content" / "plugins" / "marketplace",)
FRONTEND = ROOT / "frontend"
HARNESS = FRONTEND / "tests" / "effects" / "parity-shaders.mjs"
SHADER_DRIFT = {"max_abs": 1, "mean_abs": 0.02}
BROWSER_TIMEOUT_SECONDS = 600.0
BROWSER_PROBE = "const { chromium } = await import('playwright'); const browser = await chromium.launch(); await browser.close();"


@dataclass(frozen=True)
class Target:
    effect: EffectSpec
    golden: Path


@dataclass
class Row:
    tool: str
    side: str
    case: int
    metric: str
    score: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)
    note: str = ""


class HarnessUnavailable(RuntimeError):
    pass


def core_targets() -> list[Target]:
    loaded = EffectLoader().load_root(CORE_ROOT, CORE_SOURCE)
    if loaded.errors:
        raise ParityError("; ".join(f"{name}: {message}" for name, message in loaded.errors))
    return [Target(effect, CORE_GOLDEN / effect.tool_id) for effect in loaded.effects]


def _effect_roots(manifest: dict) -> list[str]:
    roots = []
    for entry in manifest.get("effects") or []:
        path = entry.get("path") if isinstance(entry, dict) else entry
        if isinstance(path, str) and path:
            roots.append(path)
    return roots


def plugin_targets(plugin_dir: Path) -> list[Target]:
    plugin_dir = Path(plugin_dir).resolve()
    manifest_path = plugin_dir / "manifest.yml"
    if not manifest_path.is_file():
        raise ParityError(f"{plugin_dir} has no manifest.yml")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    source = str(manifest.get("id") or plugin_dir.name)
    targets = []
    for relative in _effect_roots(manifest):
        root = (plugin_dir / relative).resolve()
        if plugin_dir not in root.parents and root != plugin_dir:
            raise ParityError(f"{source}: effects path {relative} leaves the plugin folder")
        loaded = EffectLoader().load_root(root, source)
        if loaded.errors:
            raise ParityError("; ".join(f"{source}/{name}: {message}" for name, message in loaded.errors))
        targets += [Target(effect, plugin_dir / "tests" / "effects" / "golden" / effect.tool_id) for effect in loaded.effects]
    return targets


def discover(plugin_dirs: Optional[Iterable[Path]] = None, trees: Iterable[Path] = PLUGIN_TREES) -> list[Target]:
    if plugin_dirs:
        return [target for folder in plugin_dirs for target in plugin_targets(Path(folder))]
    targets = core_targets()
    for tree in trees:
        if not Path(tree).is_dir():
            continue
        for folder in sorted(Path(tree).iterdir()):
            if (folder / "manifest.yml").is_file():
                targets += plugin_targets(folder)
    return targets


def tool_payload(effect: EffectSpec) -> dict:
    return {**effect_tool_spec(effect).to_dict(), "runnable": True, "last_params": None, "effect": effect_block(effect)}


def _rgba(rgb: np.ndarray) -> str:
    alpha = np.full((*rgb.shape[:2], 1), 255, dtype=np.uint8)
    return base64.b64encode(np.concatenate([rgb, alpha], axis=-1).tobytes()).decode("ascii")


def shader_job(effect: EffectSpec) -> dict:
    media = parity_media(effect)
    cases = []
    for case in parity_cases(effect):
        params = EffectTool.public(case_params(effect, case))
        cases.append({"params": params, "analysis": case.analysis, "frames": list(case.frames)})
    return {
        "tool": tool_payload(effect),
        "shader": effect.shader,
        "width": media.width,
        "height": media.height,
        "fps": media.fps,
        "duration": media.duration,
        "input": _rgba(fixture_frame(media.width, media.height)),
        "cases": cases,
    }


def harness_ready() -> Optional[str]:
    node = shutil.which("node")
    if node is None:
        return "node is not installed"
    for package in ("esbuild", "playwright"):
        if not (FRONTEND / "node_modules" / package / "package.json").is_file():
            return f"frontend/node_modules/{package} is missing"
    try:
        completed = subprocess.run(
            [node, "--input-type=module", "-e", BROWSER_PROBE],
            cwd=FRONTEND, capture_output=True, text=True, timeout=60, env=dict(os.environ),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not ask Playwright for its browser: {exc}"
    if completed.returncode != 0:
        return "Playwright's Chromium does not start (npx playwright install chromium)"
    return None


def render_shaders(targets: list[Target]) -> tuple[str, dict[str, dict[tuple[int, int], np.ndarray]]]:
    problem = harness_ready()
    if problem:
        raise HarnessUnavailable(problem)
    shaded = [target for target in targets if target.effect.shader is not None]
    with tempfile.TemporaryDirectory(prefix="effect-parity-") as scratch:
        job_path, out_path = Path(scratch) / "job.json", Path(scratch) / "out.json"
        payload = {"effects": [{"key": target.effect.tool_id, "job": shader_job(target.effect)} for target in shaded]}
        job_path.write_text(json.dumps(payload), encoding="utf-8")
        completed = subprocess.run(
            [shutil.which("node"), str(HARNESS), str(job_path), str(out_path)],
            cwd=FRONTEND, capture_output=True, text=True, timeout=BROWSER_TIMEOUT_SECONDS, env=dict(os.environ),
        )
        if completed.returncode != 0:
            raise ParityError(f"shader harness failed: {(completed.stderr or completed.stdout).strip()}")
        result = json.loads(out_path.read_text(encoding="utf-8"))
    rendered: dict[str, dict[tuple[int, int], np.ndarray]] = {}
    for target in shaded:
        media = parity_media(target.effect)
        frames = {}
        for entry in result["results"][target.effect.tool_id]:
            raw = np.frombuffer(base64.b64decode(entry["pixels"]), dtype=np.uint8)
            frames[(entry["case"], entry["frame"])] = raw.reshape(media.height, media.width, 4)[:, :, :3].copy()
        rendered[target.effect.tool_id] = frames
    return result["renderer"], rendered


def golden_paths(target: Target) -> dict[tuple[int, int], Path]:
    return {
        (case.index, frame): target.golden / golden_name(case, frame)
        for case in parity_cases(target.effect)
        for frame in case.frames
    }


def write_goldens(targets: list[Target], rendered: dict) -> list[Path]:
    written = []
    for target in targets:
        if target.effect.tool_id not in rendered:
            continue
        paths = golden_paths(target)
        if target.golden.is_dir():
            for stale in target.golden.glob("c*_f*.png"):
                if stale not in paths.values():
                    stale.unlink()
        for key, path in paths.items():
            write_golden(path, rendered[target.effect.tool_id][key])
            written.append(path)
    return written


def missing_goldens(target: Target) -> list[Path]:
    if target.effect.shader is None:
        return []
    return [path for path in golden_paths(target).values() if not path.is_file()]


def load_case_goldens(target: Target, case: ParityCase) -> dict[int, np.ndarray]:
    return {frame: read_golden(target.golden / golden_name(case, frame)) for frame in case.frames}


def candidate_ready(candidate: Candidate) -> Optional[str]:
    if candidate.kind == "ffmpeg":
        if not find_ffmpeg():
            return "ffmpeg is not installed"
        missing = candidate.template.filters() - installed_filters()
        return f"ffmpeg lacks {', '.join(sorted(missing))}" if missing else None
    if candidate.kind == "torch" and not torch_installed():
        return "torch is not installed"
    return None


def check_candidate(target: Target, candidate: Candidate) -> list[Row]:
    effect = target.effect
    metric = parity_metric(effect)
    tolerance = effect.parity["tolerance"]
    media = parity_media(effect)
    source = fixture_frame(media.width, media.height)
    rows = []
    for case in parity_cases(effect):
        row = Row(tool=effect.tool_id, side=candidate.kind, case=case.index, metric=metric)
        expected = load_case_goldens(target, case)
        actual = render_case(effect, candidate, case, source)
        row.score = score_case(metric, expected, actual, source)
        row.failures = over_tolerance(row.score, tolerance)
        rows.append(row)
    return rows


def check_candidates(targets: list[Target]) -> list[Row]:
    rows = []
    for target in targets:
        effect = target.effect
        if effect.shader is None:
            rows.append(Row(tool=effect.tool_id, side="-", case=-1, metric="-", note="no shader, nothing to compare"))
            continue
        missing = missing_goldens(target)
        if missing:
            rows.append(Row(
                tool=effect.tool_id, side="golden", case=-1, metric="-",
                failures=[f"{len(missing)} goldens missing; run scripts/effect_parity.py --golden"],
            ))
            continue
        for candidate in effect.render:
            problem = candidate_ready(candidate)
            if problem:
                rows.append(Row(tool=effect.tool_id, side=candidate.kind, case=-1, metric="-", note=f"skipped: {problem}"))
                continue
            rows += check_candidate(target, candidate)
    return rows


def check_shader_drift(targets: list[Target], rendered: dict) -> list[Row]:
    rows = []
    for target in targets:
        frames = rendered.get(target.effect.tool_id)
        if frames is None:
            continue
        media = parity_media(target.effect)
        source = fixture_frame(media.width, media.height)
        for case in parity_cases(target.effect):
            row = Row(tool=target.effect.tool_id, side="shader", case=case.index, metric="pixels")
            missing = [frame for frame in case.frames if not (target.golden / golden_name(case, frame)).is_file()]
            if missing:
                row.failures = [f"goldens missing for frames {missing}"]
            else:
                actual = {frame: frames[(case.index, frame)] for frame in case.frames}
                row.score = score_case("pixels", load_case_goldens(target, case), actual, source)
                row.failures = over_tolerance(row.score, SHADER_DRIFT)
            rows.append(row)
    return rows


def format_rows(rows: list[Row]) -> str:
    lines = [f"{'effect':<22} {'side':<7} {'case':>4}  {'metric':<6}  numbers"]
    for row in rows:
        numbers = "  ".join(f"{key}={value:.4g}" for key, value in row.score.items()) or row.note
        status = "FAIL " + "; ".join(row.failures) if row.failures else ("ok" if row.score else "")
        case = str(row.case) if row.case >= 0 else "-"
        lines.append(f"{row.tool:<22} {row.side:<7} {case:>4}  {row.metric:<6}  {numbers}  {status}".rstrip())
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that every effect's renders match its live preview shader.",
    )
    parser.add_argument("plugins", nargs="*", type=Path, help="plugin folders to check instead of the app's effects")
    parser.add_argument("--golden", action="store_true", help="render the shaders and rewrite the goldens")
    parser.add_argument("--check-shaders", action="store_true", help="render the shaders and fail on drift")
    parser.add_argument("--effect", action="append", default=[], help="only this effect id (repeatable)")
    args = parser.parse_args(argv)

    targets = discover(args.plugins or None)
    if args.effect:
        targets = [target for target in targets if target.effect.tool_id in args.effect]
    rows: list[Row] = []
    if args.golden or args.check_shaders:
        renderer, rendered = render_shaders(targets)
        print(f"shader renderer: {renderer}")
        if args.golden:
            written = write_goldens(targets, rendered)
            print(f"wrote {len(written)} goldens")
        else:
            rows += check_shader_drift(targets, rendered)
    rows += check_candidates(targets)
    print(format_rows(rows))
    return 1 if any(row.failures for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
