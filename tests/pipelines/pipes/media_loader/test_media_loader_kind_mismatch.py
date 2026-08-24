"""Tests for the expected-kind guard in MediaLoaderPipe: a pipeline stage that
configures an explicit `type` (image/video/audio) for a media item is stating a
hard requirement of the pipeline, not a hint - the file the form field (or an
upload endpoint, or a stale value) actually points at must match. See
main.py::process for why this cannot be trusted from upstream alone.
"""

from __future__ import annotations

import pytest

from src.pipelines.pipes.media_loader.main import MediaLoaderPipe


def _pipe(**config_over):
    cfg = MediaLoaderPipe.get_default_config()
    cfg.update(config_over)
    return MediaLoaderPipe(config=cfg)


def _touch(tmp_path, name) -> str:
    path = tmp_path / name
    path.write_bytes(b"fake bytes")
    return str(path)


def test_video_stage_rejects_image_file(tmp_path):
    path = _touch(tmp_path, "source.png")
    pipe = _pipe(media=[{"type": "video", "path": path}])
    with pytest.raises(ValueError, match="expects a video file"):
        pipe.process(None, lambda o: None)


def test_image_stage_rejects_video_file(tmp_path):
    path = _touch(tmp_path, "source.mp4")
    pipe = _pipe(media=[{"type": "image", "path": path}])
    with pytest.raises(ValueError, match="expects a image file"):
        pipe.process(None, lambda o: None)


def test_video_stage_rejects_audio_file(tmp_path):
    path = _touch(tmp_path, "source.mp3")
    pipe = _pipe(media=[{"type": "video", "path": path}])
    with pytest.raises(ValueError, match="expects a video file"):
        pipe.process(None, lambda o: None)


def test_matching_kind_still_loads(tmp_path):
    path = _touch(tmp_path, "source.mp4")
    pipe = _pipe(media=[{"type": "video", "path": path}])
    result = pipe.process(None, lambda o: None)
    assert result.output["video"] == [path]


def test_unrecognized_extension_is_not_treated_as_mismatch(tmp_path):
    # An extension the pipe's format lists don't know at all can't be proven
    # wrong - only a confirmed OTHER kind should fail fast.
    path = _touch(tmp_path, "source.unknownext")
    pipe = _pipe(media=[{"type": "video", "path": path}])
    result = pipe.process(None, lambda o: None)
    assert result.output["video"] == [path]


def test_auto_detected_type_is_never_flagged_as_mismatch(tmp_path):
    path = _touch(tmp_path, "source.mp4")
    pipe = _pipe(media=[{"path": path}], auto_detect_type=True)  # no explicit type
    result = pipe.process(None, lambda o: None)
    assert result.output["video"] == [path]
