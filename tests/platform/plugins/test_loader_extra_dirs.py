import logging
import os
from pathlib import Path

import pytest

from src.platform.plugins.loader import EXTRA_PLUGIN_DIRS_ENV, PluginLoader


def write_plugin(root: Path, folder: str, plugin_id: str) -> Path:
    plugin_dir = root / folder
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.yml").write_text(
        "\n".join([
            f'id: "{plugin_id}"',
            f'name: "{plugin_id}"',
            'version: "1.0.0"',
            'description: "test plugin"',
            'author: "tests"',
            'type: "backend-only"',
            'category: "other"',
        ]) + "\n",
        encoding="utf-8",
    )
    return plugin_dir


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.delenv(EXTRA_PLUGIN_DIRS_ENV, raising=False)
    marketplace, local = tmp_path / "marketplace", tmp_path / "local"
    marketplace.mkdir()
    local.mkdir()
    return marketplace, local


def discover(roots, *extra):
    os.environ[EXTRA_PLUGIN_DIRS_ENV] = os.pathsep.join(str(path) for path in extra)
    try:
        return PluginLoader(str(roots[0]), str(roots[1])).discover_plugins()
    finally:
        del os.environ[EXTRA_PLUGIN_DIRS_ENV]


def test_an_extra_directory_adds_its_plugins_as_local_ones(roots, tmp_path):
    write_plugin(tmp_path / "extra", "alpha", "alpha")

    (found,) = discover(roots, tmp_path / "extra")

    assert (found.id, found.source) == ("alpha", "local")


def test_a_missing_extra_directory_is_skipped_with_a_warning(roots, tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        found = discover(roots, tmp_path / "nowhere")

    assert found == []
    assert any("does not exist" in record.getMessage() for record in caplog.records)


def test_a_missing_directory_does_not_hide_the_ones_after_it(roots, tmp_path):
    write_plugin(tmp_path / "extra", "alpha", "alpha")

    found = discover(roots, tmp_path / "nowhere", tmp_path / "extra")

    assert [m.id for m in found] == ["alpha"]


@pytest.mark.parametrize("value", ["", "   ", os.pathsep, os.pathsep * 3])
def test_an_empty_value_adds_no_directories(roots, monkeypatch, value):
    monkeypatch.setenv(EXTRA_PLUGIN_DIRS_ENV, value)

    assert PluginLoader(str(roots[0]), str(roots[1])).extra_dirs == []


def test_a_trailing_separator_does_not_add_the_working_directory(roots, tmp_path, monkeypatch):
    write_plugin(tmp_path / "extra", "alpha", "alpha")
    monkeypatch.setenv(EXTRA_PLUGIN_DIRS_ENV, f"{tmp_path / 'extra'}{os.pathsep}")

    loader = PluginLoader(str(roots[0]), str(roots[1]))

    assert loader.extra_dirs == [tmp_path / "extra"]
    assert [m.id for m in loader.discover_plugins()] == ["alpha"]


def test_the_local_directory_wins_over_an_extra_directory_with_the_same_id(roots, tmp_path, caplog):
    local_copy = write_plugin(roots[1], "dup", "dup")
    write_plugin(tmp_path / "extra", "dup", "dup")

    with caplog.at_level(logging.WARNING):
        found = discover(roots, tmp_path / "extra")

    (winner,) = found
    assert Path(winner.plugin_dir) == local_copy
    assert any("already provided" in record.getMessage() and "dup" in record.getMessage() for record in caplog.records)


def test_the_first_extra_directory_wins_over_a_later_one(roots, tmp_path, caplog):
    first = write_plugin(tmp_path / "one", "dup", "dup")
    write_plugin(tmp_path / "two", "dup", "dup")

    with caplog.at_level(logging.WARNING):
        found = discover(roots, tmp_path / "one", tmp_path / "two")

    (winner,) = found
    assert Path(winner.plugin_dir) == first
    assert any("skipping the extra-directory copy" in record.getMessage() for record in caplog.records)


def test_an_extra_plugin_shadows_a_marketplace_plugin_like_a_local_one(roots, tmp_path):
    hidden = write_plugin(roots[0], "dup", "dup")
    extra = write_plugin(tmp_path / "extra", "dup", "dup")

    (winner,) = discover(roots, tmp_path / "extra")

    assert Path(winner.plugin_dir) == extra and winner.source == "local"
    assert Path(winner.shadows) == hidden


def test_a_local_plugin_keeps_shadowing_marketplace_when_an_extra_copy_is_skipped(roots, tmp_path):
    hidden = write_plugin(roots[0], "dup", "dup")
    local_copy = write_plugin(roots[1], "dup", "dup")
    write_plugin(tmp_path / "extra", "dup", "dup")

    (winner,) = discover(roots, tmp_path / "extra")

    assert Path(winner.plugin_dir) == local_copy and Path(winner.shadows) == hidden


def test_different_ids_from_every_root_are_all_found(roots, tmp_path):
    write_plugin(roots[0], "m", "m")
    write_plugin(roots[1], "l", "l")
    write_plugin(tmp_path / "extra", "e", "e")

    assert sorted(m.id for m in discover(roots, tmp_path / "extra")) == ["e", "l", "m"]


def test_without_the_variable_nothing_extra_is_scanned(roots):
    assert PluginLoader(str(roots[0]), str(roots[1])).extra_dirs == []
