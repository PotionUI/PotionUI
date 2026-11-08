from __future__ import annotations

import pytest
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.media_loader.main import MediaLoaderPipe


class _Settings:
    def __init__(self, root: str):
        self._root = root

    def get_file_storage_directory(self, user_id=None) -> str:
        return self._root


def _pipe(media):
    cfg = MediaLoaderPipe.get_default_config()
    cfg["media"] = media
    return MediaLoaderPipe(config=cfg)


def _run(pipe, storage):
    return pipe.process(PipeInput(input={"SETTINGS": _Settings(str(storage))}), lambda o: None)


def _upload(storage, name="bottle.png"):
    path = storage / "uploads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 24), "purple").save(path)
    return path


def _ref(path, relative):
    return {
        "path": path,
        "relative_path": relative,
        "url": "/api/media/" + relative,
        "name": "bottle.png",
        "type": "image",
        "metadata": {"width": 32, "height": 24},
    }


def test_an_upload_reference_as_path_loads_the_image(tmp_path):
    storage = tmp_path / "storage"
    absolute = _upload(storage)
    result = _run(_pipe([{"type": "image", "path": _ref(str(absolute), "uploads/bottle.png")}]), storage)
    assert len(result.output["image"]) == 1
    assert result.output["image"][0].size == (32, 24)
    assert result.output["media_metadata"][0]["path"] == str(absolute)


def test_a_stale_absolute_path_falls_back_to_the_relative_path(tmp_path):
    storage = tmp_path / "storage"
    absolute = _upload(storage)
    ref = _ref(str(tmp_path / "old-root" / "uploads" / "bottle.png"), "uploads/bottle.png")
    result = _run(_pipe([{"type": "image", "path": ref}]), storage)
    assert len(result.output["image"]) == 1
    assert result.output["media_metadata"][0]["path"] == str(absolute)


def test_a_reference_without_a_local_path_is_refused(tmp_path):
    ref = {"url": "/api/media/uploads/bottle.png", "type": "image"}
    with pytest.raises(ValueError, match="no local path"):
        _run(_pipe([{"type": "image", "path": ref}]), tmp_path)


def test_a_path_that_is_neither_text_nor_a_reference_is_refused(tmp_path):
    with pytest.raises(ValueError, match="must be a string or a media reference"):
        _run(_pipe([{"type": "image", "path": ["uploads/bottle.png"]}]), tmp_path)


def test_a_missing_referenced_file_still_raises_the_missing_file_error(tmp_path):
    ref = _ref(str(tmp_path / "nowhere.png"), "uploads/nowhere.png")
    with pytest.raises(OSError, match="missing or unreadable"):
        _run(_pipe([{"type": "image", "path": ref}]), tmp_path / "storage")
