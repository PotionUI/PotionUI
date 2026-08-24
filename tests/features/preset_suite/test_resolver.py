"""Tests for the preset-suite model resolver (no network, no real models)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from src.features.preset_suite.resolver import ModelResolver


def _ref(sha, hf=None):
    return SimpleNamespace(sha256=sha, hf=hf)


def _write(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


# --- (1) DB hit ---------------------------------------------------------------


def test_db_hit_returns_file_path(tmp_path):
    f = tmp_path / "models" / "sd.safetensors"
    sha = _write(f, b"weights")
    repo = SimpleNamespace(get_by_sha256=lambda s: SimpleNamespace(file_path=str(f)) if s == sha else None)
    r = ModelResolver(tmp_path / "models", model_repository=repo, cache_path=tmp_path / "c.json")
    res = r.resolve(_ref(sha))
    assert res.resolved and res.source == "db" and res.file_path == str(f)


def test_db_row_with_missing_file_falls_through(tmp_path):
    # DB says a path that no longer exists -> ignore the row, fall to hash-walk.
    f = tmp_path / "models" / "real.bin"
    sha = _write(f, b"real")
    repo = SimpleNamespace(get_by_sha256=lambda s: SimpleNamespace(file_path=str(tmp_path / "gone.bin")))
    r = ModelResolver(tmp_path / "models", model_repository=repo, cache_path=tmp_path / "c.json")
    res = r.resolve(_ref(sha))
    assert res.resolved and res.source == "hash-walk"


# --- (0) live sha-index -------------------------------------------------------


def test_sha_index_hit_short_circuits_before_db_and_walk(tmp_path):
    # The in-memory live snapshot is the fastest path: it must resolve without
    # ever consulting the (ephemeral) DB or hashing the tree.
    f = tmp_path / "models" / "checkpoints" / "sd.safetensors"
    sha = _write(f, b"weights")

    def _boom(_s):
        raise AssertionError("DB must not be consulted when sha-index hits")

    r = ModelResolver(
        tmp_path / "models",
        model_repository=SimpleNamespace(get_by_sha256=_boom),
        sha_index={sha.upper(): str(f)},  # case-insensitive
        cache_path=tmp_path / "c.json",
    )
    res = r.resolve(_ref(sha))
    assert res.resolved and res.source == "db-index" and res.file_path == str(f)


def test_sha_index_with_vanished_file_falls_through(tmp_path):
    # An index entry whose file no longer exists must not resolve — fall through.
    f = tmp_path / "models" / "real.bin"
    sha = _write(f, b"real")
    r = ModelResolver(
        tmp_path / "models",
        sha_index={sha: str(tmp_path / "gone.bin")},
        cache_path=tmp_path / "c.json",
    )
    res = r.resolve(_ref(sha))
    assert res.resolved and res.source == "hash-walk"


# --- (2) hash-walk + cache ----------------------------------------------------


def test_hash_walk_finds_by_sha(tmp_path):
    f = tmp_path / "models" / "sub" / "vae.pt"
    sha = _write(f, b"vae-bytes")
    r = ModelResolver(tmp_path / "models", cache_path=tmp_path / "c.json")
    res = r.resolve(_ref(sha))
    assert res.resolved and res.source == "hash-walk" and res.file_path == str(f)


def test_hash_cache_is_written_and_reused(tmp_path):
    f = tmp_path / "models" / "a.bin"
    sha = _write(f, b"aaaa")
    cache = tmp_path / "c.json"
    calls = {"n": 0}

    def _counting_hasher(p):
        calls["n"] += 1
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    r1 = ModelResolver(tmp_path / "models", cache_path=cache, hasher=_counting_hasher)
    assert r1.resolve(_ref(sha)).resolved
    assert calls["n"] == 1 and cache.exists()

    # A fresh resolver reads the cache -> no re-hash for the unchanged file.
    r2 = ModelResolver(tmp_path / "models", cache_path=cache, hasher=_counting_hasher)
    assert r2.resolve(_ref(sha)).resolved
    assert calls["n"] == 1, "unchanged file should not be re-hashed"


def test_hash_cache_invalidates_on_mtime_change(tmp_path):
    f = tmp_path / "models" / "a.bin"
    sha1 = _write(f, b"first")
    cache = tmp_path / "c.json"
    r = ModelResolver(tmp_path / "models", cache_path=cache)
    r.resolve(_ref(sha1))

    # Overwrite with new content + bump mtime -> the cached hash must be discarded.
    import os, time
    time.sleep(0.01)
    sha2 = _write(f, b"second-different-length")
    os.utime(f, None)
    r2 = ModelResolver(tmp_path / "models", cache_path=cache)
    assert r2.resolve(_ref(sha2)).resolved            # new content found
    assert not r2.resolve(_ref(sha1)).resolved        # stale hash no longer matches


# --- (3) download gating ------------------------------------------------------


def test_missing_without_permission_is_skip_not_fail(tmp_path):
    (tmp_path / "models").mkdir()
    r = ModelResolver(tmp_path / "models", cache_path=tmp_path / "c.json", allow_download=False)
    res = r.resolve(_ref("deadbeef" * 8, hf={"repo": "org/model", "file": "m.safetensors"}))
    assert not res.resolved and "not found locally" in res.reason and "org/model" in res.reason


def test_missing_no_hf_ref_skip(tmp_path):
    (tmp_path / "models").mkdir()
    r = ModelResolver(tmp_path / "models", cache_path=tmp_path / "c.json", allow_download=True)
    res = r.resolve(_ref("beefbeef" * 8, hf=None))
    assert not res.resolved and "no HF ref" in res.reason


def test_download_happy_path_verifies_sha(tmp_path):
    dest_hint = {}

    def _fake_dl(repo, file, dest_dir):
        p = Path(dest_dir) / file
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"downloaded-weights")
        dest_hint["dir"] = Path(dest_dir)
        return str(p)

    sha = hashlib.sha256(b"downloaded-weights").hexdigest()
    r = ModelResolver(tmp_path / "models", cache_path=tmp_path / "c.json",
                      allow_download=True, downloader=_fake_dl)
    res = r.resolve(_ref(sha, hf={"repo": "org/m", "file": "w.safetensors"}), model_type="loras")
    assert res.resolved and res.source == "download"
    assert dest_hint["dir"].name == "loras"          # model_type hint used as subdir


def test_download_sha_mismatch_is_skip(tmp_path):
    def _fake_dl(repo, file, dest_dir):
        p = Path(dest_dir) / file
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"WRONG")
        return str(p)

    r = ModelResolver(tmp_path / "models", cache_path=tmp_path / "c.json",
                      allow_download=True, downloader=_fake_dl)
    res = r.resolve(_ref("a" * 64, hf={"repo": "o/m", "file": "w"}))
    assert not res.resolved and "!=" in res.reason    # sha mismatch reported


def test_download_failure_is_skip(tmp_path):
    def _boom(repo, file, dest_dir):
        raise RuntimeError("network down")

    r = ModelResolver(tmp_path / "models", cache_path=tmp_path / "c.json",
                      allow_download=True, downloader=_boom)
    res = r.resolve(_ref("b" * 64, hf={"repo": "o/m", "file": "w"}))
    assert not res.resolved and "download failed" in res.reason


def test_no_sha_is_skip(tmp_path):
    r = ModelResolver(tmp_path / "models", cache_path=tmp_path / "c.json")
    assert not r.resolve(_ref("")).resolved


# --- (3b) download routed through the core download queue ---------------------
#
# A missing model used to be fetched with a direct `huggingface_hub.
# hf_hub_download` call, bypassing the download queue (`src.features.downloads.
# DownloadManager`) entirely - no depot configuration, no admin history, no
# progress. When a `download_manager` is injected it must be used instead of
# that direct call; `downloader=` (mainly for the tests above) still wins when
# both are given.


def test_download_manager_used_when_no_explicit_downloader(tmp_path):
    data = b"downloaded-weights"
    sha = hashlib.sha256(data).hexdigest()
    dest_file = tmp_path / "models" / "loras" / "w.safetensors"
    calls = {}

    async def _queue(**kwargs):
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        dest_file.write_bytes(data)
        calls["queue_kwargs"] = kwargs
        return SimpleNamespace(id="d1")

    def _get_download(download_id):
        assert download_id == "d1"
        return SimpleNamespace(status="completed", destination_path=str(dest_file), error_message=None)

    dm = SimpleNamespace(queue_model_download=_queue, get_download=_get_download)

    r = ModelResolver(tmp_path / "models", cache_path=tmp_path / "c.json",
                       allow_download=True, download_manager=dm)
    res = r.resolve(_ref(sha, hf={"repo": "org/m", "file": "w.safetensors"}), model_type="loras")

    assert res.resolved and res.source == "download"
    assert res.file_path == str(dest_file)
    assert calls["queue_kwargs"]["url"] == "https://huggingface.co/org/m/resolve/main/w.safetensors"
    assert calls["queue_kwargs"]["filename"] == "w.safetensors"
    assert Path(calls["queue_kwargs"]["destination_dir"]).name == "loras"


def test_explicit_downloader_wins_over_download_manager(tmp_path):
    manager_calls = {"n": 0}

    async def _queue(**kwargs):
        manager_calls["n"] += 1
        return SimpleNamespace(id="d1")

    dm = SimpleNamespace(queue_model_download=_queue, get_download=lambda did: None)

    def _fake_dl(repo, file, dest_dir):
        p = Path(dest_dir) / file
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        return str(p)

    sha = hashlib.sha256(b"x").hexdigest()
    r = ModelResolver(tmp_path / "models", cache_path=tmp_path / "c.json",
                       allow_download=True, downloader=_fake_dl, download_manager=dm)
    res = r.resolve(_ref(sha, hf={"repo": "o/m", "file": "w"}))

    assert res.resolved and res.source == "download"
    assert manager_calls["n"] == 0, "the explicit `downloader` must win, not the injected download_manager"


def test_download_manager_failed_status_is_skip(tmp_path):
    async def _queue(**kwargs):
        return SimpleNamespace(id="d1")

    dm = SimpleNamespace(
        queue_model_download=_queue,
        get_download=lambda did: SimpleNamespace(status="failed", destination_path=None, error_message="disk full"),
    )
    r = ModelResolver(tmp_path / "models", cache_path=tmp_path / "c.json",
                       allow_download=True, download_manager=dm)
    res = r.resolve(_ref("a" * 64, hf={"repo": "o/m", "file": "w"}))

    assert not res.resolved
    assert "download failed" in res.reason
    assert "disk full" in res.reason


def test_neither_downloader_nor_manager_falls_back_to_direct_hf_download(tmp_path, monkeypatch):
    """No `downloader` and no `download_manager` given: the legacy direct
    `huggingface_hub.hf_hub_download` fallback is still exercised (used by
    tests above via `downloader=`, and here directly to prove the fallback
    itself still works when nothing is injected)."""
    import src.features.preset_suite.resolver as resolver_mod

    calls = {}

    def _fake_hub_download(repo_id, filename, local_dir):
        calls["repo_id"] = repo_id
        calls["filename"] = filename
        p = Path(local_dir) / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"hub-weights")
        return str(p)

    monkeypatch.setattr(
        resolver_mod, "_hf_download",
        lambda repo, file, dest_dir: _fake_hub_download(repo, file, dest_dir),
    )

    sha = hashlib.sha256(b"hub-weights").hexdigest()
    r = ModelResolver(tmp_path / "models", cache_path=tmp_path / "c.json", allow_download=True)
    res = r.resolve(_ref(sha, hf={"repo": "o/m", "file": "w.bin"}))

    assert res.resolved and res.source == "download"
    assert calls == {"repo_id": "o/m", "filename": "w.bin"}
