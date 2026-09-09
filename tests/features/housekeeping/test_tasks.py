"""The scratch-folder pass: what it removes, what it refuses to touch, and the
estimate that has to agree with it."""

import os
import time
from pathlib import Path

from src.features.housekeeping.tasks import prune_tmp, scan_tmp

DAY = 86400


def _write(path: Path, content: bytes = b"x" * 100, age_days: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if age_days:
        when = time.time() - age_days * DAY
        os.utime(path, (when, when))
    return path


class TestPruneTmp:

    def test_removes_files_older_than_the_window_and_keeps_the_rest(self, tmp_path):
        old = _write(tmp_path / "old.png", age_days=30)
        fresh = _write(tmp_path / "fresh.png", age_days=1)

        result = prune_tmp(str(tmp_path), 7)

        assert not old.exists()
        assert fresh.exists()
        assert result["removed"] == 1
        assert result["bytes_freed"] == 100
        assert result["errors"] == []

    def test_a_file_exactly_inside_the_window_survives(self, tmp_path):
        kept = _write(tmp_path / "edge.png", age_days=6.9)

        prune_tmp(str(tmp_path), 7)

        assert kept.exists()

    def test_zero_days_disables_the_pass(self, tmp_path):
        ancient = _write(tmp_path / "ancient.png", age_days=900)

        result = prune_tmp(str(tmp_path), 0)

        assert ancient.exists()
        assert result == {"removed": 0, "bytes_freed": 0, "errors": []}

    def test_a_symlink_to_an_old_file_outside_the_root_is_left_alone(self, tmp_path):
        outside = _write(tmp_path / "outside" / "keepme.png", age_days=90)
        root = tmp_path / "tmp"
        root.mkdir()
        link = root / "link.png"
        link.symlink_to(outside)
        # The link itself is aged too, so the pass reaches its symlink guards
        # instead of stopping at the modification time it reads through it.
        when = time.time() - 90 * DAY
        os.utime(link, (when, when), follow_symlinks=False)

        result = prune_tmp(str(root), 7)

        assert outside.exists()
        assert link.is_symlink()
        assert result["removed"] == 0

    def test_a_symlinked_directory_is_not_walked_into(self, tmp_path):
        outside = _write(tmp_path / "outside" / "old.png", age_days=90)
        root = tmp_path / "tmp"
        root.mkdir()
        (root / "escape").symlink_to(tmp_path / "outside", target_is_directory=True)

        result = prune_tmp(str(root), 7)

        assert outside.exists()
        assert result["removed"] == 0

    def test_it_prunes_nested_files_and_drops_the_directories_it_emptied(self, tmp_path):
        _write(tmp_path / "a" / "b" / "old.png", age_days=30)

        result = prune_tmp(str(tmp_path), 7)

        assert result["removed"] == 1
        assert not (tmp_path / "a").exists()
        assert tmp_path.exists()

    def test_a_directory_that_still_holds_a_fresh_file_stays(self, tmp_path):
        _write(tmp_path / "a" / "old.png", age_days=30)
        fresh = _write(tmp_path / "a" / "fresh.png", age_days=1)

        prune_tmp(str(tmp_path), 7)

        assert fresh.exists()
        assert (tmp_path / "a").is_dir()

    def test_a_file_it_cannot_remove_is_reported_not_raised(self, tmp_path, monkeypatch):
        _write(tmp_path / "old.png", age_days=30)
        monkeypatch.setattr(
            Path, "unlink", lambda self, *a, **k: (_ for _ in ()).throw(OSError("busy"))
        )

        result = prune_tmp(str(tmp_path), 7)

        assert result["removed"] == 0
        assert len(result["errors"]) == 1
        assert "old.png" in result["errors"][0]

    def test_a_missing_root_is_not_an_error(self, tmp_path):
        assert prune_tmp(str(tmp_path / "nope"), 7) == {
            "removed": 0, "bytes_freed": 0, "errors": [],
        }


class TestScanTmp:

    def test_the_estimate_matches_what_the_pass_then_removes(self, tmp_path):
        _write(tmp_path / "old.png", b"x" * 40, age_days=30)
        _write(tmp_path / "nested" / "older.png", b"x" * 60, age_days=90)
        _write(tmp_path / "fresh.png", age_days=1)

        estimate = scan_tmp(str(tmp_path), 7)
        result = prune_tmp(str(tmp_path), 7)

        assert estimate == {"files": 2, "bytes": 100}
        assert (result["removed"], result["bytes_freed"]) == (2, 100)

    def test_zero_days_estimates_nothing(self, tmp_path):
        _write(tmp_path / "old.png", age_days=900)

        assert scan_tmp(str(tmp_path), 0) == {"files": 0, "bytes": 0}

    def test_the_estimate_removes_nothing(self, tmp_path):
        old = _write(tmp_path / "old.png", age_days=30)

        scan_tmp(str(tmp_path), 7)

        assert old.exists()
