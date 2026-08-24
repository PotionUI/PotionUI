"""Tests for MediaLoaderPipe's relative-path resolution.

Two relative-path conventions reach the pipe: CWD-relative values that
include the storage prefix (the upload flow stores 'storage/uploads/...')
and storage-root-relative values (the DB stores generation outputs as
'generations/<date>/<id>/<n>.ext', copied verbatim by the history picker).
The pipe must try both bases - regression for the prod failure where a
history-picked reference image ('generations/.../0.png') resolved against
the process CWD and raised even though the file existed under
storage/generations/.
"""

from __future__ import annotations

import os

import pytest

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.media_loader.main import MediaLoaderPipe


class _Settings:
    def __init__(self, root: str):
        self._root = root

    def get_file_storage_directory(self, user_id=None) -> str:
        return self._root


def _pipe(**config_over):
    cfg = MediaLoaderPipe.get_default_config()
    cfg.update(config_over)
    return MediaLoaderPipe(config=cfg)


def _write(root, rel) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake bytes")
    return str(path)


def test_storage_root_relative_path_resolves(tmp_path):
    absolute = _write(tmp_path / "storage", "generations/2026-08-12/GEN01/0.mp4")
    pipe = _pipe(media=[{"type": "video", "path": "generations/2026-08-12/GEN01/0.mp4"}])
    result = pipe.process(PipeInput(input={"SETTINGS": _Settings(str(tmp_path / "storage"))}), lambda o: None)
    assert result.output["video"] == [absolute]


def test_cwd_relative_path_wins_over_storage_join(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "storage/uploads/clip.mp4")
    pipe = _pipe(media=[{"type": "video", "path": "storage/uploads/clip.mp4"}])
    result = pipe.process(PipeInput(input={"SETTINGS": _Settings(str(tmp_path / "storage"))}), lambda o: None)
    assert result.output["video"] == ["storage/uploads/clip.mp4"]


def test_absolute_path_is_never_rerooted(tmp_path):
    absolute = _write(tmp_path, "elsewhere/clip.mp4")
    pipe = _pipe(media=[{"type": "video", "path": absolute}])
    result = pipe.process(PipeInput(input={"SETTINGS": _Settings(str(tmp_path / "storage"))}), lambda o: None)
    assert result.output["video"] == [absolute]


def test_missing_in_both_bases_raises_listing_both(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pipe = _pipe(media=[{"type": "video", "path": "generations/2026-08-12/GEN01/0.png"}])
    with pytest.raises(OSError, match="also tried under the storage root"):
        pipe.process(PipeInput(input={"SETTINGS": _Settings(str(tmp_path / "storage"))}), lambda o: None)


def test_no_settings_service_keeps_old_behavior(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "storage/uploads/clip.mp4")
    pipe = _pipe(media=[{"type": "video", "path": "storage/uploads/clip.mp4"}])
    result = pipe.process(None, lambda o: None)
    assert result.output["video"] == ["storage/uploads/clip.mp4"]
