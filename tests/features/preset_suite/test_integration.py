"""End-to-end integration of the preset suite (runner → checks → report) against
a fake generation client — no GPU, no real models. Validates the whole pipeline
a real run exercises, minus the composition root (the user's first real run)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from src.features.preset_suite.models import CaseOutcome, FAIL, PASS, SKIP
from src.features.preset_suite.report import write_run
from src.features.preset_suite.resolver import ResolveResult
from src.features.preset_suite.runner import PresetSuiteRunner


def _gradient():
    a = np.tile(np.linspace(0, 255, 48, dtype=np.uint8), (48, 1))
    return Image.fromarray(np.stack([a, a, a], -1))


def _black():
    return Image.fromarray(np.zeros((48, 48, 3), dtype=np.uint8))


def _checks():
    return SimpleNamespace(min_outputs=1, resolution=None, not_black=True, max_seconds=None)


class _Client:
    """Per-case scripted outcomes keyed by case name."""

    def __init__(self, outcomes):
        self._outcomes = outcomes

    def can_run(self, preset_id, engine):
        return True, ""

    def run_case(self, preset_id, mode, form_data, *, prompt=None, negative_prompt=None, max_seconds=None):
        # name is threaded via form for this fake
        return self._outcomes[form_data["__name__"]]


class _Resolver:
    def resolve(self, ref, *, model_type=None):
        # 'good' shas resolve; anything else is a SKIP
        if getattr(ref, "sha256", "") == "good":
            return ResolveResult("/models/x.safetensors", source="db")
        return ResolveResult(None, reason="not found locally and --allow-download not set")


def test_full_run_writes_gallery_with_pass_fail_skip(tmp_path):
    root = tmp_path / "presets"
    pd = root / "native" / "Demo" / "std"
    pd.mkdir(parents=True)
    (pd / "tests.yml").write_text("x")
    (pd / "preset.yml").write_text("engine: native\nid: 01DEMOULID0000000000000000\n")

    def _case(name, images_key, model_sha="good"):
        return SimpleNamespace(
            name=name, mode="txt2img", seed=7, tags=["fast"], checks=_checks(),
            form={"__name__": name},
            models={"checkpoint": SimpleNamespace(sha256=model_sha, hf=None)},
        )

    cases = [
        _case("passes", "grad"),
        _case("black_fail", "black"),
        _case("missing_model_skip", "grad", model_sha="absent"),
    ]

    def _loader(preset_dir):
        return SimpleNamespace(cases=cases)

    outcomes = {
        "passes": CaseOutcome(status="completed", images=[_gradient()], seconds=1.2),
        "black_fail": CaseOutcome(status="completed", images=[_black()], seconds=1.0),
        # missing_model_skip never reaches the client (resolver SKIPs it first)
    }
    runner = PresetSuiteRunner(_Client(outcomes), _Resolver(), presets_root=root, loader=_loader)
    results = runner.run()

    by_name = {r.case_name: r for r in results}
    assert by_name["passes"].verdict == PASS
    assert by_name["black_fail"].verdict == FAIL
    assert by_name["missing_model_skip"].verdict == SKIP

    run_dir = tmp_path / "test-runs" / "run1"
    index = write_run(run_dir, results)

    assert index.exists()
    html = index.read_text()
    assert "PASS" in html and "FAIL" in html and "SKIP" in html
    # The PASS case wrote an image the gallery references.
    passed = by_name["passes"]
    assert passed.image_paths, "no image paths recorded for the passing case"
    assert (run_dir / passed.image_paths[0]).exists()
    # Metadata carries the submitted form + verdict.
    meta = json.loads((run_dir / passed.image_paths[0]).parent.joinpath("metadata.json").read_text())
    assert meta["verdict"] == PASS and meta["submitted_form"]["checkpoint"] == "/models/x.safetensors"
