#!/usr/bin/env python3
"""Render a preset's `styles.yml` previews.

Runs one real generation per style through the same headless orchestrator
client `scripts/preset_test_suite.py` uses
(`src.features.preset_suite.runner.HeadlessGenerationClient`) - an ephemeral
per-run database and file storage, so the run never touches the maintainer's
live database or gallery (see that module's docstring for the ephemeral-DB
design). Downscales the first output image to `--long-edge` px, saves it as
`public/styles/<id>.webp` inside the preset directory, and fills `preview:`
in `styles.yml` for that style when it was unset.

    python scripts/preset_styles_render.py content/presets/marketplace/Anima
    python scripts/preset_styles_render.py content/presets/marketplace/Anima --style retro-90s-cel
    python scripts/preset_styles_render.py 01KX5GRNWFC9S2F6T15155H41C --long-edge 512 --seed 7 --force

The preset argument is either a preset id (its `preset.yml` `id:`) or the
preset's directory (the one holding its `preset.yml`, e.g.
`content/presets/marketplace/Anima`).

Style rendering has no form of its own to pick model weights from, so it
borrows the preset's `tests.yml`: the first case's `models:` map (see
docs/presets.md "Testing presets") is resolved, read-only, against the live
models table before anything else runs, and the resolved file paths are
submitted with every style's generation unchanged. A preset with no
`tests.yml`, no cases, an empty `models:` map on the first case, or any ref
that isn't already present locally (this script never downloads) exits 1
before rendering anything, naming exactly what's missing.

Prints one line per style (`ok <id> 23 KB` / `skip <id> (preview exists)` /
`FAILED <id>: <error>`) and exits nonzero if any style failed (a skip does
not fail the run).

This script never reloads or restarts anything - the running app picks up
the rendered files (and any `preview:` fill) on its own next preset reload
or restart.

NOTE: a real run loads models and generates on the GPU. During development
this is never invoked automatically; the prompt/downscale/`styles.yml` logic
is unit-tested with a mocked generation client in
`tests/features/presets/test_style_previews.py` and
`tests/scripts/test_preset_styles_render.py` - the first real run is the
user's.
"""

from __future__ import annotations

import argparse
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
    """Build the headless generation client and force it to boot, returning
    ``(client, container)``. The container is captured from the factory
    closure rather than read off the client - `HeadlessGenerationClient`
    only exposes its container as an implementation detail, and a closure
    keeps this script from reaching into that."""
    from src.features.preset_suite.runner import HeadlessGenerationClient

    built: Dict[str, Any] = {}

    def _factory():
        from src.bootstrap.container import build_container

        built["container"] = build_container()
        return built["container"]

    client = HeadlessGenerationClient(_factory, run_dir=run_dir)
    # `can_run` boots the client as a side effect; the engine/preset_id here
    # are placeholders only used by the native fast path, which ignores both.
    client.can_run("bootstrap", "native")
    return client, built["container"]


def _resolve_preset(preset_loader, ref: str):
    """A preset by its `preset.yml` id, or by its directory (matched against
    each loaded preset's resolved `path`)."""
    preset = preset_loader.load_preset_by_id(ref)
    if preset is not None:
        return preset

    target = Path(ref).resolve()
    for candidate in preset_loader.presets:
        if Path(candidate.path).resolve() == target:
            return candidate
    return None


def resolve_model_form_data(preset, resolver) -> Optional[Dict[str, str]]:
    """The preset's model fields (e.g. `diffusion_model`, `text_encoder`),
    resolved to local file paths - style rendering has no form of its own to
    pick models from, so it borrows the preset's `tests.yml`: the first
    case's `models:` map (see docs/presets.md "Testing presets"). Prints an
    `error: ...` line and returns `None` - no rendering is attempted - when
    the preset has no `tests.yml`/no cases, its first case declares no
    `models:`, or any ref can't be resolved locally (no downloads here; the
    caller never passes `allow_download`)."""
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
) -> List[str]:
    """Render `style_ids` (all of the preset's styles when omitted); returns
    the report lines, one per style (skips included). `model_form_data` is
    the preset's model fields, already resolved by `resolve_model_form_data`,
    submitted unchanged with every generation."""
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

    for style in targets:
        rel_preview = preview_rel_path(style["id"])
        dest = preset_dir / rel_preview

        if dest.exists() and not force:
            lines.append(f"skip {style['id']} (preview exists)")
            continue

        prompt, negative = build_style_prompt(style)
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
    args = ap.parse_args(argv)

    from src.features.preset_suite import ephemeral

    # The resolver reads the REAL models table (read-only) to LOCATE model
    # files on disk; this must happen BEFORE the client re-points the DB
    # singleton at its ephemeral copy inside `_boot_client` - same ordering
    # `scripts/preset_test_suite.py` uses (see `build_live_resolver`).
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
                client, preset, args.style_ids, args.long_edge, args.seed, args.force, model_form_data
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


if __name__ == "__main__":
    raise SystemExit(main())
