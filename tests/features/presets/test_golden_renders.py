"""
Golden-snapshot guard for the preset-render developer harness (Step 0 of the
templating-system rework).

Re-renders every preset x mode via the exact same code path as
`scripts/preset_render.py --golden-all` and asserts the result matches the
stored snapshot in tests/golden/preset_renders/. A currently-broken render is
itself part of the contract: if a preset/mode fails to render today, its
snapshot records {"error": "..."} and this test asserts that error is still
raised - a silent fix (or break) of that render is exactly what this guard
exists to catch.

This is pure template rendering (no models, no GPU, no DB, no app boot) so it
runs in a couple of seconds.

To regenerate the snapshots after an intentional rendering-behavior change:
    source ./venv/bin/activate
    PYTHONPATH=./venv/lib/python3.12/site-packages:. python3 scripts/preset_render.py --golden-all

To verify without regenerating (what this test does):
    PYTHONPATH=./venv/lib/python3.12/site-packages:. python -m pytest tests/core/preset/test_golden_renders.py -v
"""

import json
from pathlib import Path

import pytest

from scripts.preset_render import (
    build_form_serializer,
    build_processor,
    golden_filename,
    load_all_presets,
    render_preset_mode,
)

GOLDEN_DIR = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "golden" / "preset_renders"


def _load_all_records():
    # include_plugins=False: goldens are a repo artifact, and neither
    # plugin-shipped presets nor plugin-contributed modes are repo-tracked --
    # a local plugin on this machine must not change the set this guard
    # demands (or flags as stale).
    presets, _load_errors = load_all_presets(include_plugins=False)
    processor = build_processor()
    form_serializer = build_form_serializer()

    records = []
    for preset in sorted(presets, key=lambda p: p.id):
        for mode in sorted(preset.modes.keys()):
            record = render_preset_mode(processor, form_serializer, preset, mode)
            records.append((preset, mode, record))
    return records


# Rendering all 45 preset x mode combinations is cheap (pure Jinja rendering,
# no models/GPU/DB) - computed once per test session and reused by every
# parametrized case below.
@pytest.fixture(scope="module")
def all_records():
    return _load_all_records()


def _record_ids(records):
    return [f"{preset.id}::{mode}" for preset, mode, _ in records]


class TestGoldenRenders:
    def test_golden_snapshot_directory_is_not_empty(self):
        assert GOLDEN_DIR.exists(), f"Golden snapshot directory missing: {GOLDEN_DIR}"
        snapshots = list(GOLDEN_DIR.glob("*.json"))
        assert len(snapshots) > 0, "No golden snapshots found - run scripts/preset_render.py --golden-all"

    def test_every_rendered_preset_mode_has_a_snapshot_file(self, all_records):
        for preset, mode, _record in all_records:
            path = GOLDEN_DIR / golden_filename(preset, mode)
            assert path.exists(), (
                f"Missing golden snapshot for {preset.name!r} ({preset.id}) mode {mode!r}: {path}\n"
                f"Regenerate with: python scripts/preset_render.py --golden-all"
            )

    def test_no_stale_snapshot_files(self, all_records):
        expected = {golden_filename(preset, mode) for preset, mode, _ in all_records}
        actual = {p.name for p in GOLDEN_DIR.glob("*.json")}
        stale = actual - expected
        assert not stale, (
            f"Stale golden snapshot file(s) with no matching preset/mode anymore: {sorted(stale)}\n"
            f"Regenerate with: python scripts/preset_render.py --golden-all"
        )

    def test_rendered_output_matches_golden_snapshot(self, all_records):
        mismatches = []
        for preset, mode, record in all_records:
            path = GOLDEN_DIR / golden_filename(preset, mode)
            if not path.exists():
                # Already reported by test_every_rendered_preset_mode_has_a_snapshot_file
                continue

            expected = json.loads(path.read_text())
            if record != expected:
                mismatches.append(_diff_message(preset, mode, expected, record))

        assert not mismatches, (
            "Preset render output drifted from the golden snapshot(s):\n\n"
            + "\n\n".join(mismatches)
            + "\n\nIf this drift is intentional, regenerate with:\n"
            "  PYTHONPATH=./venv/lib/python3.12/site-packages:. python3 scripts/preset_render.py --golden-all"
        )


def _diff_message(preset, mode, expected, actual) -> str:
    label = f"{preset.name!r} ({preset.id}) / {mode!r}"

    if "error" in expected or "error" in actual:
        return (
            f"{label}:\n"
            f"  expected error: {expected.get('error', '<none - rendered pipes instead>')}\n"
            f"  actual error:   {actual.get('error', '<none - rendered pipes instead>')}"
        )

    expected_pipes = {p["id"] or p["name"]: p for p in expected.get("pipes", [])}
    actual_pipes = {p["id"] or p["name"]: p for p in actual.get("pipes", [])}

    lines = [label]
    for key in sorted(set(expected_pipes) | set(actual_pipes)):
        exp_pipe = expected_pipes.get(key)
        act_pipe = actual_pipes.get(key)
        if exp_pipe is None:
            lines.append(f"  pipe {key!r}: only in ACTUAL")
            continue
        if act_pipe is None:
            lines.append(f"  pipe {key!r}: only in EXPECTED")
            continue
        if exp_pipe == act_pipe:
            continue

        if exp_pipe["enabled"] != act_pipe["enabled"]:
            lines.append(f"  pipe {key!r}.enabled: expected {exp_pipe['enabled']} != actual {act_pipe['enabled']}")

        exp_cfg = exp_pipe.get("config", {})
        act_cfg = act_pipe.get("config", {})
        for path in sorted(set(exp_cfg) | set(act_cfg)):
            if exp_cfg.get(path) != act_cfg.get(path):
                lines.append(
                    f"  pipe {key!r}.config.{path}: expected {exp_cfg.get(path)!r} != actual {act_cfg.get(path)!r}"
                )

    return "\n".join(lines)
