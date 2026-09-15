from src.pipelines.pipes.detailer.sdxl.main import ADetailerSDXLPipe


def _pipe(model):
    return ADetailerSDXLPipe(config={"detect": ["face"], "detections": {"face": {"enabled": True, "model": model}}})


def test_unset_model_is_reported_missing():
    assert _pipe(None)._missing_detector_model("face") == "(unset)"
    assert _pipe("None")._missing_detector_model("face") == "(unset)"


def test_absent_file_is_reported_missing(tmp_path):
    missing = tmp_path / "face.pt"
    assert _pipe(str(missing))._missing_detector_model("face") == str(missing)


def test_present_file_is_not_missing(tmp_path):
    present = tmp_path / "face.pt"
    present.write_bytes(b"")
    assert _pipe(str(present))._missing_detector_model("face") is None
