"""Tests for the native-LLM checkpoint directory scan.

`list_native_checkpoints`/`resolve_native_checkpoint_path` are a lightweight
filesystem scan, deliberately NOT wired into the DB-backed models catalog
(see native_library.py's module docstring for why) - these tests cover the
scan/resolve contract directly, no database involved.
"""

from __future__ import annotations

import json

import pytest

from src.features.llm.native_library import (
    list_native_checkpoints,
    resolve_native_checkpoint_path,
)
from tests.fixtures.model_roots import make_roots


def _resolver(tmp_path):
    return make_roots(tmp_path, types=["llm"], home_dir=tmp_path)


def _write_checkpoint(base, name: str, model_type: str | None):
    d = base / "llm" / name
    d.mkdir(parents=True)
    if model_type is not None:
        (d / "config.json").write_text(json.dumps({"model_type": model_type}))
    return d


def test_list_native_checkpoints_empty_when_dir_missing(tmp_path):
    assert list_native_checkpoints(_resolver(tmp_path)) == []


def test_list_native_checkpoints_skips_dirs_without_config_json(tmp_path):
    d = tmp_path / "llm" / "not-a-checkpoint"
    d.mkdir(parents=True)
    (d / "readme.txt").write_text("nothing here")
    assert list_native_checkpoints(_resolver(tmp_path)) == []


def test_list_native_checkpoints_flags_supported_text_family(tmp_path):
    _write_checkpoint(tmp_path, "qwen3-dense", "qwen3")
    entries = list_native_checkpoints(_resolver(tmp_path))
    assert len(entries) == 1
    assert entries[0].name == "qwen3-dense"
    assert entries[0].model_type == "qwen3"
    assert entries[0].supported is True
    assert entries[0].vision is False
    assert entries[0].reason is None


def test_list_native_checkpoints_flags_supported_vision_family(tmp_path):
    _write_checkpoint(tmp_path, "qwen3-vl", "qwen3_vl")
    entries = list_native_checkpoints(_resolver(tmp_path))
    assert entries[0].vision is True
    assert entries[0].supported is True


def test_list_native_checkpoints_flags_unsupported_family_with_reason(tmp_path):
    _write_checkpoint(tmp_path, "llama-not-supported", "llama")
    entries = list_native_checkpoints(_resolver(tmp_path))
    assert entries[0].supported is False
    assert "llama" in entries[0].reason
    assert "qwen3" in entries[0].reason  # names the allowlist


def test_list_native_checkpoints_offers_quant_modes_only_when_supported(tmp_path):
    from src.features.llm.native_library import NATIVE_LLM_QUANT_MODES

    _write_checkpoint(tmp_path, "qwen3-dense", "qwen3")
    _write_checkpoint(tmp_path, "llama-nope", "llama")
    by_name = {e.name: e for e in list_native_checkpoints(_resolver(tmp_path))}
    assert by_name["qwen3-dense"].quant_modes == list(NATIVE_LLM_QUANT_MODES)
    assert by_name["llama-nope"].quant_modes == []


def test_list_native_checkpoints_flags_unreadable_config(tmp_path):
    d = tmp_path / "llm" / "corrupt"
    d.mkdir(parents=True)
    (d / "config.json").write_text("{not valid json")
    entries = list_native_checkpoints(_resolver(tmp_path))
    assert entries[0].supported is False
    assert entries[0].model_type is None
    assert "could not read" in entries[0].reason


def test_list_native_checkpoints_sorted_and_multiple(tmp_path):
    _write_checkpoint(tmp_path, "zeta", "qwen3")
    _write_checkpoint(tmp_path, "alpha", "gemma3_text")
    entries = list_native_checkpoints(_resolver(tmp_path))
    assert [e.name for e in entries] == ["alpha", "zeta"]


def test_list_native_checkpoints_first_root_wins_on_duplicate_name(tmp_path):
    home = tmp_path / "home"
    lib = tmp_path / "lib"
    _write_checkpoint(home, "qwen3-dense", "qwen3")
    _write_checkpoint(lib, "qwen3-dense", "gemma3_text")

    resolver = make_roots(tmp_path, types=["llm"], home_dir=home)
    from src.platform.filesystem.model_roots import ModelRoot, TypeDir, _Snapshot

    lib_root = ModelRoot(
        id="lib", label="lib", path=lib, kind="library", read_only=False,
        case_insensitive=False, state="online", state_reason=None, raw_path=str(lib),
    )
    home_root = resolver.roots()[0]
    lib_type_dir = TypeDir(root_id="lib", model_type="llm", path=lib / "llm", position=1, is_write=False)
    home_type_dir = TypeDir(root_id="home", model_type="llm", path=home / "llm", position=0, is_write=True)
    resolver._snapshot = _Snapshot(
        roots=(home_root, lib_root),
        roots_by_id={"home": home_root, "lib": lib_root},
        type_dirs_by_type={"llm": (home_type_dir, lib_type_dir)},
    )

    entries = list_native_checkpoints(resolver)
    assert len(entries) == 1
    assert entries[0].model_type == "qwen3"


def test_resolve_native_checkpoint_path_success(tmp_path):
    d = _write_checkpoint(tmp_path, "qwen3-dense", "qwen3")
    resolved = resolve_native_checkpoint_path("qwen3-dense", _resolver(tmp_path))
    assert resolved == str(d.resolve())


def test_resolve_native_checkpoint_path_missing_raises(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        resolve_native_checkpoint_path("does-not-exist", _resolver(tmp_path))


def test_resolve_native_checkpoint_path_rejects_traversal(tmp_path):
    with pytest.raises(ValueError, match="Invalid native LLM checkpoint name"):
        resolve_native_checkpoint_path("../../etc", _resolver(tmp_path))
