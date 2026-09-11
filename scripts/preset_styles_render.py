#!/usr/bin/env python3
"""Render a preset's `styles.yml` previews.

    python scripts/preset_styles_render.py content/presets/marketplace/Anima
    python scripts/preset_styles_render.py content/presets/marketplace/Anima --style retro-90s-cel
    python scripts/preset_styles_render.py 01KX5GRNWFC9S2F6T15155H41C --long-edge 512 --seed 7 --force
    python scripts/preset_styles_render.py content/presets/marketplace/Anima --steps 30 --resolution 1024x1024

Resolves each style's model weights from the preset's `tests.yml` (first
case's `models:` map), never downloads, runs through the same
HeadlessGenerationClient as scripts/preset_test_suite.py (ephemeral DB and
storage - never the maintainer's live database or gallery), downscales the
first output image, writes public/styles/<id>.webp, and fills `preview:` in
styles.yml when it was unset. Prints one line per style and exits nonzero if
any failed. Never reloads or restarts the app.

NOTE: a real run loads models and generates on the GPU, never invoked
automatically in development - see tests/features/presets/test_style_previews.py
and tests/scripts/test_preset_styles_render.py.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.presets.style_previews import (  # noqa: E402
    STYLE_PREVIEW_LONG_EDGE_DEFAULT,
    STYLE_PREVIEW_QUANTITY_FIELD,
    STYLE_PREVIEW_SEED_DEFAULT,
    build_style_prompt,
    downscale_and_save_webp,
    form_has_field,
    preview_rel_path,
    select_styles,
    set_style_preview,
)
from src.features.preset_suite.resolver_factory import build_live_resolver  # noqa: E402
from src.features.preset_suite.runner import _model_type_hint  # noqa: E402
from src.features.presets.tests_schema import load_tests_yml  # noqa: E402


def _boot_client(run_dir: Path):
    from src.features.preset_suite.runner import HeadlessGenerationClient

    built: Dict[str, Any] = {}

    def _factory():
        from src.bootstrap.container import build_container

        built["container"] = build_container()
        return built["container"]

    client = HeadlessGenerationClient(_factory, run_dir=run_dir)
    client.can_run("bootstrap", "native")
    return client, built["container"]


def _resolve_preset(preset_loader, ref: str):
    preset = preset_loader.load_preset_by_id(ref)
    if preset is not None:
        return preset

    target = Path(ref).resolve()
    for candidate in preset_loader.presets:
        if Path(candidate.path).resolve() == target:
            return candidate
    return None


def resolve_model_form_data(preset, resolver) -> Optional[Dict[str, str]]:
    tests = load_tests_yml(Path(preset.path))
    if tests is None or not tests.cases:
        print(
            f"error: preset '{preset.id}' has no tests.yml (or it declares no cases) - "
            "style rendering resolves its model fields from the first case's models: map"
        )
        return None

    models = tests.cases[0].models
    if not models:
        print(
            f"error: preset '{preset.id}' tests.yml's first case declares no models: - "
            "style rendering needs it to know which weights to use"
        )
        return None

    resolved: Dict[str, str] = {}
    ok = True
    for field_name, ref in models.items():
        result = resolver.resolve(ref, model_type=_model_type_hint(field_name))
        if not result.resolved:
            print(f"error: model '{field_name}': {result.reason}")
            ok = False
            continue
        resolved[field_name] = result.file_path

    return resolved if ok else None


def render_styles(
    client,
    preset,
    style_ids: Optional[List[str]],
    long_edge: int,
    seed: int,
    force: bool,
    model_form_data: Dict[str, str],
    steps: Optional[int] = None,
    resolution: Optional[str] = None,
) -> List[str]:
    lines: List[str] = []
    targets = select_styles(preset.styles or [], style_ids)

    if not preset.modes:
        raise ValueError(f"preset '{preset.id}' has no modes")
    mode = next(iter(preset.modes))

    engine = preset.engine or "native"
    ok, reason = client.can_run(preset.id, engine)
    if not ok:
        return [f"FAILED {style['id']}: {reason}" for style in targets]

    preset_dir = Path(preset.path)
    styles_yml = preset_dir / "styles.yml"

    form_data: Dict[str, Any] = {"seed": seed, **model_form_data}
    if form_has_field(preset, mode, STYLE_PREVIEW_QUANTITY_FIELD):
        form_data[STYLE_PREVIEW_QUANTITY_FIELD] = 1

    effective_steps: Any = "preset default"
    if steps is not None:
        if form_has_field(preset, mode, "steps"):
            form_data["steps"] = steps
            effective_steps = steps
        else:
            effective_steps = "preset default (--steps ignored, no 'steps' field)"

    effective_resolution: Any = "preset default"
    if resolution is not None:
        if form_has_field(preset, mode, "resolution"):
            form_data["resolution"] = resolution
            effective_resolution = resolution
        else:
            effective_resolution = "preset default (--resolution ignored, no 'resolution' field)"

    print(f"steps: {effective_steps}; resolution: {effective_resolution}")

    for style in targets:
        rel_preview = preview_rel_path(style["id"])
        dest = preset_dir / rel_preview

        if dest.exists() and not force:
            lines.append(f"skip {style['id']} (preview exists)")
            continue

        prompt, negative = build_style_prompt(style, preset.styles_preview)
        outcome = client.run_case(preset.id, mode, dict(form_data), prompt=prompt, negative_prompt=negative)

        if outcome.status != "completed" or not outcome.images:
            error = outcome.error or "generation produced no output image"
            lines.append(f"FAILED {style['id']}: {error}")
            continue

        downscale_and_save_webp(outcome.images[0], dest, long_edge)
        size_kb = dest.stat().st_size / 1024
        lines.append(f"ok {style['id']} {size_kb:.0f} KB")

        if not style.get("preview"):
            set_style_preview(styles_yml, style["id"], rel_preview)

    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render a preset's styles.yml previews.")
    ap.add_argument("preset", help="Preset id (preset.yml id:) or directory.")
    ap.add_argument("--style", action="append", dest="style_ids", metavar="ID",
                     help="Render only this style id (repeatable). Default: every style.")
    ap.add_argument("--long-edge", type=int, default=STYLE_PREVIEW_LONG_EDGE_DEFAULT,
                     help=f"Downscale target, in px (default: {STYLE_PREVIEW_LONG_EDGE_DEFAULT}).")
    ap.add_argument("--seed", type=int, default=STYLE_PREVIEW_SEED_DEFAULT,
                     help=f"Generation seed (default: {STYLE_PREVIEW_SEED_DEFAULT}).")
    ap.add_argument("--force", action="store_true",
                     help="Re-render a style even when its preview file already exists.")
    ap.add_argument("--steps", type=int, default=None,
                     help="Override the preset's steps, only if its form has a 'steps' field.")
    ap.add_argument("--resolution", default=None, metavar="WxH",
                     help="Override the preset's resolution, only if its form has a 'resolution' field.")
    args = ap.parse_args(argv)

    from src.features.preset_suite import ephemeral

    # Must run before _boot_client re-points the DB singleton at its ephemeral copy.
    resolver = build_live_resolver(allow_download=False)

    run_dir = Path(tempfile.mkdtemp(prefix="potionui-style-preview-"))
    ephemeral.mark(run_dir)

    try:
        client, container = _boot_client(run_dir)

        preset = _resolve_preset(container.preset_template_loader, args.preset)
        if preset is None:
            print(f"error: preset '{args.preset}' not found")
            return 1
        if not preset.styles:
            print(f"error: preset '{preset.id}' has no styles.yml")
            return 1

        model_form_data = resolve_model_form_data(preset, resolver)
        if model_form_data is None:
            return 1

        try:
            lines = render_styles(
                client, preset, args.style_ids, args.long_edge, args.seed, args.force, model_form_data,
                steps=args.steps, resolution=args.resolution,
            )
        except ValueError as e:
            print(f"error: {e}")
            return 1

        n_failed = 0
        for line in lines:
            print(line)
            if line.startswith("FAILED "):
                n_failed += 1

        print(f"\n{len(lines)} style(s): {n_failed} failed")
        return 1 if n_failed else 0
    finally:
        ephemeral.cleanup(
            run_dir,
            [run_dir / "suite.db", run_dir / "suite.db-wal", run_dir / "suite.db-shm", run_dir / "storage"],
            keep=False, failed=False,
        )
        # cleanup() doesn't remove run_dir itself.
        if ephemeral.is_marked(run_dir):
            shutil.rmtree(run_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
