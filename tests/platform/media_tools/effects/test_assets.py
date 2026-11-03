from __future__ import annotations

import hashlib
import sys

import pytest

from src.platform.imaging.filters.cube import Cube
from src.platform.imaging.filters.lut import lattice, serialize_cube
from src.platform.media_tools.effects import assets
from src.platform.media_tools.effects.assets import stage_cube
from src.platform.media_tools.effects.template import AssetPath, format_asset

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="Windows refuses these characters in folder names")


def cube(scale: float = 1.0) -> Cube:
    return Cube(size=2, data=(lattice(2) * scale).astype("float32"))


@pytest.fixture
def work(tmp_path, monkeypatch):
    folder = tmp_path / "work"
    folder.mkdir()
    monkeypatch.chdir(folder)
    return tmp_path


def test_a_folder_beside_the_working_directory_stages_as_a_safe_parent_path(work):
    relative = stage_cube(cube(), work / "scratch")

    assert relative is not None and relative.startswith("../scratch/lut-") and relative.endswith(".cube")
    assert format_asset(AssetPath(relative)) == relative
    assert (work / "work" / relative).resolve().read_text(encoding="utf-8") == serialize_cube(cube())


def test_no_relative_path_means_nothing_is_staged(work, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(assets.os.path, "relpath", refuse)

    assert stage_cube(cube(), work / "scratch") is None


@pytest.mark.parametrize(
    "name",
    [
        "with space",
        pytest.param('with"quote', marks=POSIX_ONLY),
        pytest.param("with:colon", marks=POSIX_ONLY),
        "with'apostrophe",
        "with,comma",
        "with;semicolon",
        "with[bracket]",
    ],
)
def test_a_folder_ffmpeg_could_misread_is_never_staged(work, name):
    assert stage_cube(cube(), work / name) is None


def test_the_name_is_the_content_hash_and_staging_twice_writes_once(work):
    folder = work / "scratch"
    first = stage_cube(cube(), folder)
    target = (work / "work" / first).resolve()
    target.write_text("sentinel", encoding="utf-8")

    again = stage_cube(cube(), folder)
    other = stage_cube(cube(0.5), folder)

    digest = hashlib.sha256(serialize_cube(cube()).encode("utf-8")).hexdigest()[:12]
    assert first == again and first.endswith(f"/lut-{digest}.cube")
    assert target.read_text(encoding="utf-8") == "sentinel"
    assert other != first and len(list(folder.iterdir())) == 2


@pytest.mark.parametrize(
    "name",
    ["plain", "a b", "a$b", "a%b", "a\\b", "a=b", "a|b", "a(b)", "ünï", "-dash", ".dot", "a..b", "a\tb"],
)
def test_whatever_the_folder_a_returned_path_always_passes_the_template_check(work, name):
    try:
        relative = stage_cube(cube(), work / name)
    except OSError:
        pytest.skip("this file system refuses the folder name")
    if relative is not None:
        assert format_asset(AssetPath(relative)) == relative
        assert all(part not in relative for part in (" ", "\\", "$", "%", "=", "|", "\t", ":", "'", '"'))
