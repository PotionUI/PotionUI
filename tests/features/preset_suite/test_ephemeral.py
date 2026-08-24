"""Tests for the preset-suite ephemeral cleanup safety rules."""

from __future__ import annotations

from pathlib import Path

from src.features.preset_suite import ephemeral


def _run_dir(tmp_path) -> Path:
    return ephemeral.mark(tmp_path / "test-runs" / "2026-01-01_00-00-00")


def test_mark_creates_dir_and_marker(tmp_path):
    d = _run_dir(tmp_path)
    assert d.is_dir() and (d / ephemeral.MARKER_NAME).is_file()
    assert ephemeral.is_marked(d)


def test_cleanup_removes_marked_ephemeral_paths(tmp_path):
    run_dir = _run_dir(tmp_path)
    db = run_dir / "suite.db"
    db.write_text("x")
    storage = ephemeral.mark(run_dir / "storage")
    (storage / "img.png").write_text("y")

    removed = ephemeral.cleanup(run_dir, [db, storage], keep=False, failed=False)
    assert set(removed) == {db, storage}
    assert not db.exists() and not storage.exists()


def test_cleanup_refuses_unmarked_run_dir(tmp_path):
    # A run dir WITHOUT the marker must never be touched, even if paths are passed.
    run_dir = tmp_path / "not-ours"
    run_dir.mkdir()
    victim = run_dir / "suite.db"
    victim.write_text("precious")

    removed = ephemeral.cleanup(run_dir, [victim], keep=False, failed=False)
    assert removed == [] and victim.exists()


def test_cleanup_retains_on_failure(tmp_path):
    run_dir = _run_dir(tmp_path)
    db = run_dir / "suite.db"
    db.write_text("x")
    removed = ephemeral.cleanup(run_dir, [db], keep=False, failed=True)
    assert removed == [] and db.exists()


def test_cleanup_retains_on_keep(tmp_path):
    run_dir = _run_dir(tmp_path)
    db = run_dir / "suite.db"
    db.write_text("x")
    removed = ephemeral.cleanup(run_dir, [db], keep=True, failed=False)
    assert removed == [] and db.exists()


def test_cleanup_refuses_symlink(tmp_path):
    run_dir = _run_dir(tmp_path)
    outside = tmp_path / "outside.db"
    outside.write_text("precious")
    link = run_dir / "suite.db"
    link.symlink_to(outside)

    removed = ephemeral.cleanup(run_dir, [link], keep=False, failed=False)
    assert removed == [] and outside.exists() and link.is_symlink()


def test_cleanup_refuses_path_outside_run_dir(tmp_path):
    run_dir = _run_dir(tmp_path)
    outside = tmp_path / "elsewhere" / "suite.db"
    outside.parent.mkdir()
    outside.write_text("precious")

    removed = ephemeral.cleanup(run_dir, [outside], keep=False, failed=False)
    assert removed == [] and outside.exists()
