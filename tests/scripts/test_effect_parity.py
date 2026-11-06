from __future__ import annotations

import os
import numpy as np
import pytest
import yaml

import scripts.effect_parity as ep
from src.platform.media_tools.effects.loader import core_effects
from src.platform.media_tools.effects.parity import ParityError, parity_cases, render_case
from tests.platform.media_tools.effects.builder import write_effect

FIXTURE_PLUGIN = ep.ROOT / "tests" / "e2e" / "effect_plugins" / "e2e-effects"
TARGETS = ep.discover() + ep.plugin_targets(FIXTURE_PLUGIN)
CANDIDATES = [(target, candidate) for target in TARGETS for candidate in target.effect.render]


def candidate_id(pair) -> str:
    target, candidate = pair
    return f"{target.effect.tool_id}-{candidate.kind}"


def make_plugin(root, name="glow", effects=("dim",), paths=("effects",)):
    plugin = root / name
    plugin.mkdir(parents=True)
    (plugin / "manifest.yml").write_text(
        yaml.safe_dump({"id": name, "name": name, "effects": [{"path": path} for path in paths]}), encoding="utf-8"
    )
    for effect in effects:
        write_effect(plugin / "effects", effect)
    return plugin


def numpy_goldens(target):
    candidate = target.effect.render[-1]
    return {
        target.effect.tool_id: {
            (case.index, frame): image
            for case in parity_cases(target.effect)
            for frame, image in render_case(target.effect, candidate, case).items()
        }
    }


def test_every_effect_in_the_catalog_is_a_parity_target():
    assert {effect.tool_id for effect in core_effects()} <= {target.effect.tool_id for target in TARGETS}
    assert {"vignette", "fade", "ken_burns", "film_grain", "fix_colors", "e2e-effects.invert"} <= {target.effect.tool_id for target in TARGETS}


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.effect.tool_id)
def test_every_effect_with_a_shader_has_a_golden_for_each_case_frame(target):
    assert ep.missing_goldens(target) == []


@pytest.mark.parametrize("pair", CANDIDATES, ids=candidate_id)
def test_every_render_candidate_matches_the_live_preview_goldens(pair):
    target, candidate = pair
    problem = ep.candidate_ready(candidate)
    if problem:
        pytest.skip(problem)

    rows = ep.check_candidate(target, candidate)

    assert rows and all(row.score for row in rows)
    assert [f"case {row.case}: {row.failures} {row.score}" for row in rows if row.failures] == []


def test_a_plugin_effect_is_found_from_its_manifest_and_keeps_its_goldens_in_the_plugin(tmp_path):
    plugin = make_plugin(tmp_path)

    targets = ep.discover(trees=[tmp_path])

    found = [target for target in targets if target.effect.tool_id == "glow.dim"]
    assert len(found) == 1 and found[0].golden == plugin.resolve() / "tests" / "effects" / "golden" / "glow.dim"
    assert {effect.tool_id for effect in core_effects()} <= {target.effect.tool_id for target in targets}


def test_a_new_effect_without_goldens_fails_until_they_are_written(tmp_path):
    (target,) = ep.discover([make_plugin(tmp_path)])

    rows = ep.check_candidates([target])
    assert [row.failures for row in rows] == [["1 goldens missing; run scripts/effect_parity.py --golden"]]

    ep.write_goldens([target], numpy_goldens(target))
    rows = ep.check_candidates([target])
    assert [row.side for row in rows] == ["numpy"] and not any(row.failures for row in rows)


def test_drift_between_a_render_and_its_golden_fails_the_check_and_the_cli(tmp_path, capsys):
    plugin = make_plugin(tmp_path)
    (target,) = ep.discover([plugin])
    goldens = numpy_goldens(target)
    ep.write_goldens([target], {key: {frame: np.clip(image.astype(int) + 4, 0, 255).astype(np.uint8) for frame, image in value.items()} for key, value in goldens.items()})

    rows = ep.check_candidates([target])
    assert rows[0].failures and rows[0].score["max_abs"] == 4
    assert ep.main([str(plugin)]) == 1
    assert "FAIL max_abs 4 > 1" in capsys.readouterr().out

    ep.write_goldens([target], goldens)
    assert ep.main([str(plugin)]) == 0


def test_stale_goldens_are_removed_when_they_are_rewritten(tmp_path):
    (target,) = ep.discover([make_plugin(tmp_path)])
    target.golden.mkdir(parents=True)
    (target.golden / "c3_f9.png").write_bytes(b"old")

    ep.write_goldens([target], numpy_goldens(target))

    assert sorted(path.name for path in target.golden.iterdir()) == ["c0_f0.png"]


def test_an_effects_path_may_not_leave_its_plugin(tmp_path):
    plugin = make_plugin(tmp_path, paths=("../elsewhere",))

    with pytest.raises(ParityError, match="leaves the plugin folder"):
        ep.plugin_targets(plugin)


def test_a_broken_plugin_effect_is_reported_not_skipped(tmp_path):
    plugin = make_plugin(tmp_path)
    (plugin / "effects" / "dim" / "effect.yml").write_text("id: dim\nlabel: Dim\n", encoding="utf-8")

    with pytest.raises(ParityError, match="glow/dim"):
        ep.plugin_targets(plugin)


def test_the_shader_job_carries_the_served_catalog_entry_and_full_params():
    target = next(target for target in TARGETS if target.effect.tool_id == "fade")

    job = ep.shader_job(target.effect)

    assert job["tool"]["id"] == "fade" and job["tool"]["group"] == "effects"
    assert job["tool"]["effect"]["shader"]["url"] == "/api/media-tools/effects/fade/shader"
    assert job["cases"][1]["params"] == {"fade_in": 0, "fade_out": 2, "sound": True}
    assert (job["width"], job["height"], job["fps"], job["duration"]) == (320, 180, 25.0, 4.0)
    assert len(job["input"]) == 4 * ((320 * 180 * 4 + 2) // 3)


def test_the_live_preview_shaders_still_render_their_goldens():
    problem = ep.harness_ready()
    if problem and os.environ.get(ep.REQUIRE_BROWSER_ENV) == "1":
        pytest.fail(f"shader harness unavailable and {ep.REQUIRE_BROWSER_ENV}=1: {problem}")
    if problem:
        pytest.skip(f"shader harness unavailable: {problem}")

    renderer, rendered = ep.render_shaders(TARGETS)

    rows = ep.check_shader_drift(TARGETS, rendered)

    assert "SwiftShader" in renderer
    assert {row.tool for row in rows} == {target.effect.tool_id for target in TARGETS if target.effect.shader}
    assert [f"{row.tool} case {row.case}: {row.failures}" for row in rows if row.failures] == []


def test_a_missing_harness_fails_instead_of_skipping_when_the_browser_is_required(monkeypatch):
    monkeypatch.setattr(ep, "harness_ready", lambda: "node is not installed")
    monkeypatch.setenv(ep.REQUIRE_BROWSER_ENV, "1")

    with pytest.raises(pytest.fail.Exception, match="node is not installed"):
        test_the_live_preview_shaders_still_render_their_goldens()

    monkeypatch.delenv(ep.REQUIRE_BROWSER_ENV)
    with pytest.raises(pytest.skip.Exception):
        test_the_live_preview_shaders_still_render_their_goldens()
