#!/usr/bin/env python3
"""One-command marketing media capture: records demo clips of core product
flows into webm video + screenshots, for use outside the test suite.

Boots a throwaway PotionUI instance (e2e_harness.ThrowawayApp),
seeds it (tests.e2e.marketing.seed) so it looks like a genuinely used
install, builds and serves the frontend the same way
tests/e2e/ui/run.py does (reusing its build/preview helpers rather than
re-implementing them), drives each requested scene with a real Chromium
browser via a dedicated Playwright project
(frontend/tests/e2e-marketing/), and collects the recorded `.webm` clips
into a manifest.

No system ffmpeg is required to run this script; it is required to turn the
`.webm` clips into the GIF/MP4 shot_list.md calls for - see the printed
"ffmpeg commands" section at the end of a run, and README.md next to the
manifest.

Usage:

    PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/harness/marketing_capture.py
    PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/harness/marketing_capture.py preset-picker-and-form history-gallery
    PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/harness/marketing_capture.py --skip-build
    PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/harness/marketing_capture.py --out /custom/media/dir
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from e2e_harness import StageError, ThrowawayApp, log, pick_free_port  # noqa: E402


def _load_module_by_path(name: str, path: Path):
    """Load a module by file path rather than `from tests.e2e... import` -
    site-packages can ship its own top-level `tests` package (ultralytics
    does), and PYTHONPATH puts site-packages ahead of the repo root, so a
    dotted `tests.e2e.*` import can silently resolve to the wrong package.
    Same workaround `tests/e2e/journeys/run.py` uses for journey modules, and
    `tests/e2e/ui/` (no `__init__.py` - it's driven as a standalone script)
    needs it too."""
    import importlib.util
    import sys as _sys

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # dataclasses' field-type resolution looks the module up via
    # sys.modules[cls.__module__] - it must be registered before exec_module
    # runs the class bodies, or `@dataclass` blows up on a None lookup.
    _sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_seed = _load_module_by_path("marketing_capture_seed", REPO_ROOT / "tests" / "e2e" / "marketing" / "seed.py")
seed_marketing_data = _seed.seed_marketing_data

_ui_run = _load_module_by_path("marketing_capture_ui_run", REPO_ROOT / "tests" / "e2e" / "ui" / "run.py")
PreviewMonitor = _ui_run.PreviewMonitor
run_build = _ui_run.run_build
run_playwright_watched = _ui_run.run_playwright_watched
start_preview = _ui_run.start_preview
stop_preview = _ui_run.stop_preview
wait_for_preview = _ui_run.wait_for_preview

FRONTEND_DIR = REPO_ROOT / "frontend"
MARKETING_SPECS_DIR = FRONTEND_DIR / "tests" / "e2e-marketing"
MARKETING_CONFIG = MARKETING_SPECS_DIR / "playwright.marketing.config.ts"
MARKETING_OUTPUT_DIR = MARKETING_SPECS_DIR / ".playwright-artifacts"

OWNER_USERNAME = "e2e-owner"
OWNER_PASSWORD = "marketing-capture-owner-pw-7c2f1a"

PREVIEW_START_PORT = 4273  # distinct range from tests/e2e/ui/run.py's 4173+, so a concurrent run can't collide

DEFAULT_OUTPUT_DIR = Path(tempfile.gettempdir()) / "potionui-marketing-media"

# Scenes not attempted by this script - see README.md "Skipped scenes" for
# the reasoning behind each. Kept here (not silently absent) so the manifest
# always accounts for every shot_list.md scene id.
SKIPPED_SCENES = {
    "spritesheet-editor": "multi-step plugin UI (frame extraction, BiRefNet matting, atlas layout) not yet mapped to stable selectors",
}


def discover_specs() -> List[str]:
    return sorted(p.name[: -len(".spec.ts")] for p in MARKETING_SPECS_DIR.glob("*.spec.ts"))


def check_harness_not_busy() -> None:
    try:
        out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True, check=True).stdout
    except Exception:
        return
    # Only patterns that can ONLY belong to another concurrent harness/build
    # process - this script's own invocation is deliberately not one of them
    # (its command line would always self-match, including in its bash wrapper).
    busy_patterns = ("vite build", "vite preview", "tests/e2e/ui/run.py", "npx playwright test")
    lines = [line for line in out.splitlines() if any(p in line for p in busy_patterns)]
    if lines:
        raise StageError(
            "coordination",
            "Another harness/build process looks active - refusing to start a concurrent one:\n"
            + "\n".join(lines),
        )


def run_marketing_playwright(*, backend_port: int, preview_port: int, scene_names: List[str], headed: bool) -> Optional[int]:
    base_url = f"http://127.0.0.1:{preview_port}"
    preview_log_path = MARKETING_SPECS_DIR / "preview.log"
    preview_proc = start_preview(backend_port, preview_port, preview_log_path)
    try:
        wait_for_preview(base_url)
        monitor = PreviewMonitor(preview_proc).start()

        env = dict(os.environ)
        env["E2E_BASE_URL"] = base_url
        env["E2E_BACKEND_URL"] = f"http://127.0.0.1:{backend_port}"
        env["E2E_USERNAME"] = OWNER_USERNAME
        env["E2E_PASSWORD"] = OWNER_PASSWORD

        cmd = ["npx", "playwright", "test", "--config", str(MARKETING_CONFIG), *scene_names]
        if headed:
            cmd.append("--headed")
        log(f"Running: {' '.join(cmd)}")
        returncode = run_playwright_watched(cmd, str(FRONTEND_DIR), env, monitor)

        monitor.stop()
        if monitor.died_unexpectedly:
            raise StageError("preview", f"Preview process died mid-run - see {preview_log_path}")
        return returncode
    finally:
        code = stop_preview(preview_proc)
        log(f"Preview exited: {code}")


def collect_clips(scene_names: List[str], out_dir: Path) -> Dict[str, Path]:
    """Copy each scene's recorded video.webm out of Playwright's hashed
    per-test output dirs into `out_dir/<scene>.webm`, matched by dir-name
    prefix the same way tests/e2e/ui/run.py's collect_videos does."""
    collected: Dict[str, Path] = {}
    if not MARKETING_OUTPUT_DIR.is_dir():
        return collected
    ordered = sorted(scene_names, key=len, reverse=True)
    for video in sorted(MARKETING_OUTPUT_DIR.glob("*/video.webm")):
        dir_name = video.parent.name
        scene = next((n for n in ordered if dir_name.startswith(n)), None)
        if scene is None:
            continue
        dest = out_dir / f"{scene}.webm"
        shutil.copy2(video, dest)
        collected[scene] = dest
    return collected


def probe_clip(path: Path) -> Dict[str, Optional[float]]:
    """Best-effort duration/resolution via Playwright's own bundled ffmpeg
    (downloaded alongside Chromium by `npx playwright install`; it decodes
    webm/vp8 but was built with everything else disabled, so it cannot
    transcode - see README.md). Falls back to file size only if unavailable."""
    ffmpeg_bin = None
    browsers_path = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / ".cache" / "ms-playwright")))
    if browsers_path.is_dir():
        candidates = sorted(browsers_path.glob("ffmpeg-*/ffmpeg-linux"))
        if candidates:
            ffmpeg_bin = str(candidates[0])
    info: Dict[str, Optional[float]] = {"duration_seconds": None, "width": None, "height": None}
    if not ffmpeg_bin:
        return info
    try:
        proc = subprocess.run([ffmpeg_bin, "-i", str(path)], capture_output=True, text=True, timeout=30)
        stderr = proc.stderr
        dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
        if dur_match:
            h, m, s = dur_match.groups()
            info["duration_seconds"] = round(int(h) * 3600 + int(m) * 60 + float(s), 2)
        res_match = re.search(r"Video:.*?(\d{3,5})x(\d{3,5})", stderr)
        if res_match:
            info["width"] = int(res_match.group(1))
            info["height"] = int(res_match.group(2))
    except Exception as exc:  # noqa: BLE001 - probing is best-effort, never fatal
        log(f"ffmpeg probe failed for {path.name}: {exc}")
    return info


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenes", nargs="*", help="Scene ids to capture (default: every scene discovered).")
    parser.add_argument("--skip-build", action="store_true", help="Reuse the existing frontend build output.")
    parser.add_argument("--headed", action="store_true", help="Run Chromium headed (needs a display).")
    parser.add_argument("--keep", action="store_true", help="Leave the throwaway backend + temp dir on disk after the run.")
    parser.add_argument("--port", type=int, default=None, help="Throwaway backend port (default: auto from 8055).")
    parser.add_argument("--preview-port", type=int, default=None, help="Preview port (default: auto from 4273).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory to write clips + manifest.json into.")
    args = parser.parse_args(argv)

    check_harness_not_busy()

    available = discover_specs()
    scene_names = args.scenes or available
    unknown = [s for s in scene_names if s not in available]
    if unknown:
        print(f"Unknown scene(s): {unknown}. Available: {available}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(MARKETING_OUTPUT_DIR, ignore_errors=True)

    if not args.skip_build:
        run_build()
    elif not (FRONTEND_DIR / ".svelte-kit" / "output" / "client").is_dir():
        raise StageError("build", "--skip-build given but no build output exists; run once without it.")

    manifest: Dict[str, dict] = {}
    for scene, reason in SKIPPED_SCENES.items():
        manifest[scene] = {"status": "skipped", "reason": reason}

    with ThrowawayApp(
        port=args.port, keep=args.keep, username=OWNER_USERNAME, password=OWNER_PASSWORD,
    ) as app:
        log(f"Throwaway backend up at {app.base_url} (owner={app.username})")
        seed_result = seed_marketing_data(app)
        log(
            f"Seeded {len(seed_result.generation_ids)} generation(s) "
            f"({len(seed_result.skipped_assets)} skipped), "
            f"{len(seed_result.user_ids)} user(s), group={seed_result.group_id}, "
            f"{len(seed_result.plugin_ids)} plugin(s) discovered"
        )
        if seed_result.skipped_assets:
            for reason in seed_result.skipped_assets:
                log(f"  seed skip: {reason}")

        preview_port = args.preview_port or pick_free_port(PREVIEW_START_PORT)
        returncode = run_marketing_playwright(
            backend_port=app.instance.port, preview_port=preview_port,
            scene_names=scene_names, headed=args.headed,
        )

    clips = collect_clips(scene_names, args.out)
    for scene in scene_names:
        if scene not in clips:
            manifest[scene] = {"status": "failed", "reason": "no video.webm produced - see Playwright output above"}
            continue
        path = clips[scene]
        probed = probe_clip(path)
        manifest[scene] = {
            "status": "captured",
            "file": str(path),
            "size_bytes": path.stat().st_size,
            "format": "webm",
            **probed,
        }

    manifest_path = args.out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

    print("\n=== Marketing capture manifest ===")
    for scene in sorted(manifest):
        entry = manifest[scene]
        if entry["status"] == "captured":
            dur = entry.get("duration_seconds")
            res = f"{entry.get('width')}x{entry.get('height')}" if entry.get("width") else "?"
            size_mb = entry["size_bytes"] / (1024 * 1024)
            print(f"  [captured] {scene}: {entry['file']} ({size_mb:.1f} MB, {res}, {dur}s)")
        elif entry["status"] == "skipped":
            print(f"  [skipped ] {scene}: {entry['reason']}")
        else:
            print(f"  [failed  ] {scene}: {entry['reason']}")
    print(f"\nManifest written to {manifest_path}")

    if any(e["status"] == "captured" for e in manifest.values()):
        print(
            "\nNo system ffmpeg is installed in this environment, so clips are left as .webm.\n"
            "Convert with (once ffmpeg is available):\n"
            "  ffmpeg -i <scene>.webm -vf \"fps=15,scale=800:-1:flags=lanczos,split[s0][s1];"
            "[s0]palettegen[p];[s1][p]paletteuse\" -loop 0 <scene>.gif\n"
            "  ffmpeg -i <scene>.webm -c:v libx264 -pix_fmt yuv420p -movflags +faststart <scene>.mp4"
        )

    return 1 if returncode else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except StageError as exc:
        print(f"\nFAILED at stage [{exc.stage}]: {exc.message}", file=sys.stderr)
        sys.exit(1)
