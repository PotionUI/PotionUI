"""Unit tests for the preset-suite reporter (writes to tmp_path, no GPU)."""

from __future__ import annotations

import json

import numpy as np
from PIL import Image

from src.features.preset_suite.models import FAIL, PASS, SKIP, CaseOutcome, CaseResult, CheckResult
from src.features.preset_suite.report import write_run


def _img(w=32, h=32, v=200):
    return Image.fromarray(np.full((h, w, 3), v, dtype=np.uint8), "RGB")


def _pass_case():
    outcome = CaseOutcome(
        status="completed",
        images=[_img(), _img(v=150)],
        seconds=4.2,
        submitted_form={"prompt": "a cat", "steps": 20},
    )
    return CaseResult(
        preset_id="native/SDXL/realistic",
        case_name="basic txt2img",
        verdict=PASS,
        outcome=outcome,
        checks=[CheckResult("min_outputs", True, "2 >= 1"), CheckResult("not_black", True, "ok")],
        seed=42,
        mode="txt2img",
        tags=["fast"],
    )


def _fail_case():
    outcome = CaseOutcome(
        status="failed", images=[], error="NaN detected at step 3",
        submitted_form={"prompt": "<script>alert(1)</script>"},
    )
    return CaseResult(
        preset_id="native/SDXL/realistic",
        case_name="edge/case name",
        verdict=FAIL,
        outcome=outcome,
        reason="generation failed: NaN detected at step 3",
        mode="txt2img",
    )


def _skip_case():
    outcome = CaseOutcome(status="skipped", images=[], skip_reason="model sha256 not found")
    return CaseResult(
        preset_id="comfyui/LTX-2/official",
        case_name="video short",
        verdict=SKIP,
        outcome=outcome,
        reason="model sha256 not found; pass --allow-download",
    )


def test_write_run_writes_images_metadata_and_gallery(tmp_path):
    results = [_pass_case(), _fail_case(), _skip_case()]
    index = write_run(tmp_path, results)

    assert index == tmp_path / "index.html"
    assert index.exists()

    # PASS case: dir + 2 PNGs + metadata, image_paths populated.
    pass_dir = tmp_path / "native_SDXL_realistic" / "basic_txt2img"
    assert (pass_dir / "image0.png").exists() and (pass_dir / "image1.png").exists()
    Image.open(pass_dir / "image0.png").verify()  # openable
    assert results[0].image_paths == [
        "native_SDXL_realistic/basic_txt2img/image0.png",
        "native_SDXL_realistic/basic_txt2img/image1.png",
    ]

    meta = json.loads((pass_dir / "metadata.json").read_text())
    assert meta["verdict"] == "PASS"
    assert meta["submitted_form"] == {"prompt": "a cat", "steps": 20}
    assert meta["seed"] == 42 and meta["seconds"] == 4.2
    assert [c["name"] for c in meta["checks"]] == ["min_outputs", "not_black"]
    assert meta["images"] == results[0].image_paths

    # FAIL case: dir + metadata, no images.
    fail_dir = tmp_path / "native_SDXL_realistic" / "edge_case_name"
    assert (fail_dir / "metadata.json").exists()
    assert not list(fail_dir.glob("*.png"))
    fmeta = json.loads((fail_dir / "metadata.json").read_text())
    assert fmeta["error"] == "NaN detected at step 3"

    # SKIP case dir exists too.
    assert (tmp_path / "comfyui_LTX-2_official" / "video_short" / "metadata.json").exists()


def test_index_html_has_badges_links_and_escapes(tmp_path):
    results = [_pass_case(), _fail_case(), _skip_case()]
    write_run(tmp_path, results)
    htmltext = (tmp_path / "index.html").read_text()

    assert "PASS" in htmltext and "FAIL" in htmltext and "SKIP" in htmltext
    # references the written image
    assert "native_SDXL_realistic/basic_txt2img/image0.png" in htmltext
    # the malicious prompt reaches metadata but the reason text is escaped in HTML
    assert "<script>alert(1)</script>" not in htmltext
    assert "&lt;" in htmltext or "generation failed" in htmltext
    # summary counts
    assert "1 PASS" in htmltext and "1 FAIL" in htmltext and "1 SKIP" in htmltext


def test_zero_results_writes_valid_empty_index(tmp_path):
    index = write_run(tmp_path, [])
    text = index.read_text()
    assert index.exists()
    assert "No test cases" in text
    assert text.strip().endswith("</html>")
