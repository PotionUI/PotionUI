"""The media mirror: incremental, and never destructive at the destination."""

import os

from src.features.backup.mirror import SKIP, mirror_tree


def _write(path, body: bytes, *, mtime: float = 1_600_000_000.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    os.utime(path, (mtime, mtime))
    return path


def _tree(tmp_path):
    source = tmp_path / "source"
    _write(source / "2026-09-09" / "01GEN" / "0.png", b"original")
    _write(source / "2026-09-09" / "01GEN" / "0_small.webp", b"thumb")
    _write(source / "2026-09-10" / "01GEN2" / "0.png", b"another")
    return source


def test_first_run_copies_everything(tmp_path):
    source = _tree(tmp_path)
    destination = tmp_path / "mirror"

    stats = mirror_tree(source, destination)

    assert stats.files_copied == 3
    assert stats.files_skipped == 0
    assert (destination / "2026-09-09" / "01GEN" / "0.png").read_bytes() == b"original"
    assert stats.directories["2026-09-09"] == {"files": 2, "bytes": 13, "copied": 2}


def test_second_run_copies_nothing(tmp_path):
    source = _tree(tmp_path)
    destination = tmp_path / "mirror"
    mirror_tree(source, destination)

    stats = mirror_tree(source, destination)

    assert stats.files_copied == 0
    assert stats.files_skipped == 3
    assert stats.directories["2026-09-09"]["copied"] == 0
    assert stats.directories["2026-09-09"]["files"] == 2


def test_a_changed_file_is_recopied(tmp_path):
    source = _tree(tmp_path)
    destination = tmp_path / "mirror"
    mirror_tree(source, destination)

    _write(source / "2026-09-09" / "01GEN" / "0.png", b"rewritten", mtime=1_700_000_000.0)
    stats = mirror_tree(source, destination)

    assert stats.files_copied == 1
    assert (destination / "2026-09-09" / "01GEN" / "0.png").read_bytes() == b"rewritten"


def test_animated_thumbnails_are_skipped_by_default(tmp_path):
    source = _tree(tmp_path)
    _write(source / "2026-09-09" / "01GEN" / "1_small_animated.webp", b"animated")
    destination = tmp_path / "mirror"

    stats = mirror_tree(source, destination)

    assert stats.animated_skipped_files == 1
    assert stats.animated_skipped_bytes == 8
    assert not (destination / "2026-09-09" / "01GEN" / "1_small_animated.webp").exists()


def test_animated_thumbnails_are_mirrored_when_asked(tmp_path):
    source = _tree(tmp_path)
    _write(source / "2026-09-09" / "01GEN" / "1_small_animated.webp", b"animated")
    destination = tmp_path / "mirror"

    stats = mirror_tree(source, destination, include_animated=True)

    assert stats.animated_skipped_files == 0
    assert (destination / "2026-09-09" / "01GEN" / "1_small_animated.webp").read_bytes() == b"animated"


def test_the_destination_never_loses_a_file(tmp_path):
    source = _tree(tmp_path)
    destination = tmp_path / "mirror"
    mirror_tree(source, destination)

    orphan = _write(destination / "2026-09-08" / "01OLD" / "0.png", b"deleted upstream")
    (source / "2026-09-09" / "01GEN" / "0.png").unlink()

    mirror_tree(source, destination)

    assert orphan.read_bytes() == b"deleted upstream"
    assert (destination / "2026-09-09" / "01GEN" / "0.png").read_bytes() == b"original"


def test_skip_mode_leaves_a_differing_destination_file_alone(tmp_path):
    source = _tree(tmp_path)
    destination = tmp_path / "mirror"
    _write(destination / "2026-09-09" / "01GEN" / "0.png", b"newer local copy", mtime=1_700_000_000.0)

    stats = mirror_tree(source, destination, if_exists=SKIP)

    assert (destination / "2026-09-09" / "01GEN" / "0.png").read_bytes() == b"newer local copy"
    assert stats.files_copied == 2


def test_excluded_names_inside_a_tree_are_not_mirrored(tmp_path):
    source = _tree(tmp_path)
    _write(source / "tmp" / "scratch.bin", b"scratch")
    _write(source / "2026-09-09" / "01GEN" / "0.png.bak-20260101-000000", b"stale")
    destination = tmp_path / "mirror"

    mirror_tree(source, destination)

    assert not (destination / "tmp").exists()
    assert not (destination / "2026-09-09" / "01GEN" / "0.png.bak-20260101-000000").exists()


def test_group_prefix_keeps_two_trees_apart_in_the_inventory(tmp_path):
    source = _tree(tmp_path)
    destination = tmp_path / "mirror"

    stats = mirror_tree(source, destination, group_prefix="generations/")

    assert "generations/2026-09-09" in stats.directories
    assert "2026-09-09" not in stats.directories
