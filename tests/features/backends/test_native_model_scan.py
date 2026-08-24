"""The native backend's own filesystem scan (scan_native_models) — the walk the
per-backend availability index is fed from. Regression anchor: model types added
to the models feature's scanner mappings must also exist here, or files index
nowhere despite the type "existing" (the models/vfi RIFE checkpoints hit this).

Also covers the (path, size, mtime_ns) hash cache scan_native_models now goes
through instead of hashing on every call - the fix for a file dropped into
models/checkpoints by hand having a row and no digest.
"""

import io
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import tests.conftest as ct
import src.features.models.hash_cache_repository as hash_cache_repository_module
import src.features.models.repository as model_repository_module
import src.features.tags.repository as tag_repository_module
from src.features.backends.native_model_scan import (
    DIRECTORY_TYPE_MAPPING,
    scan_native_models,
)
from src.features.models.directory import ModelIndexer
from src.features.models.indexer import ModelScanner


def _touch(path: Path, size: int = 16) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)


@pytest.fixture
def hash_cache_db():
    """A fresh migrated DB with `model_hash_cache` (migration 110), patched onto
    every module that binds `db` at import time and that the scan touches.

    `models` repository included: the scan reads `models.sha256`/`indexed_at`
    through it, and a test that leaves it bound to the real database gets an empty
    answer rather than the row it just wrote - which quietly turns a "did the scan
    trust this row?" test into one that cannot fail.
    """
    test_database = ct.TestDatabase()
    with patch("src.platform.database.database.db", test_database), \
         patch("src.platform.database.migration_runner.db", test_database), \
         patch.object(hash_cache_repository_module, "db", test_database), \
         patch.object(model_repository_module, "db", test_database), \
         patch.object(tag_repository_module, "db", test_database):
        from src.platform.database.migration_runner import MigrationManager

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            MigrationManager().run_migrations()
        finally:
            sys.stdout = old_stdout

        yield test_database
    test_database.close()


def test_vfi_directory_is_scanned(tmp_path, hash_cache_db):
    _touch(tmp_path / "vfi" / "rife46.pth")

    found = scan_native_models(str(tmp_path))

    assert [(m.model_type, m.filename) for m in found] == [("vfi", "rife46.pth")]


def test_unmapped_directory_is_skipped(tmp_path):
    """No model type mapped -> skipped before any file is even stat'd, so this
    needs no DB: nothing in this directory is ever hashed."""
    _touch(tmp_path / "not_a_model_type" / "weights.pth")

    assert scan_native_models(str(tmp_path)) == []


def test_vfi_present_in_every_type_mapping():
    assert DIRECTORY_TYPE_MAPPING.get("vfi") == "vfi"
    assert ModelScanner.MODEL_TYPE_MAPPING.get("vfi") == "vfi"
    assert ModelIndexer.TYPE_MAPPING.get("vfi") == "vfi"


# --- hash cache: what gets hashed and what doesn't -------------------------------

def test_a_never_seen_file_gets_a_real_digest(tmp_path, hash_cache_db):
    """The gap this migration closes: a file dropped in by hand, with no `models`
    row at all, must not be reported with sha256=None forever."""
    import hashlib

    path = tmp_path / "checkpoints" / "manual.safetensors"
    _touch(path, size=32)
    expected = hashlib.sha256(b"\0" * 32).hexdigest()

    found = scan_native_models(str(tmp_path))

    assert found[0].sha256 == expected
    assert found[0].confidence == "verified"


def test_rescanning_an_unchanged_file_does_not_rehash(tmp_path, hash_cache_db):
    """The performance floor: same (path, size, mtime) must be a cache hit, not a
    second read of the file's bytes."""
    path = tmp_path / "checkpoints" / "big.safetensors"
    _touch(path, size=64)

    first = scan_native_models(str(tmp_path))[0].sha256
    assert first is not None

    with patch("src.features.backends.native_model_scan._hash_file") as hash_spy:
        second = scan_native_models(str(tmp_path))[0].sha256
        hash_spy.assert_not_called()

    assert second == first


def test_a_changed_file_is_rehashed_not_trusted_stale(tmp_path, hash_cache_db):
    """The conflict-detection precondition: bytes replaced at the same path (same
    or different size) must produce a new digest, not the old cached one."""
    path = tmp_path / "checkpoints" / "swapped.safetensors"
    _touch(path, size=16)
    first = scan_native_models(str(tmp_path))[0].sha256

    # Rewrite with different content and force the mtime forward - a same-second
    # rewrite can otherwise land on an identical st_mtime_ns depending on FS
    # timestamp resolution, which would defeat the very check under test.
    path.write_bytes(b"\x01" * 16)
    new_mtime = path.stat().st_mtime_ns + 1_000_000_000
    import os
    os.utime(path, ns=(new_mtime, new_mtime))

    second = scan_native_models(str(tmp_path))[0].sha256

    assert second != first


def test_a_known_hash_at_matching_size_seeds_the_cache_instead_of_rehashing(tmp_path, hash_cache_db):
    """A file the depot-wide ModelScanner already hashed (`models.sha256`, matching
    size) must not be re-read here - it seeds the cache under today's mtime."""
    path = tmp_path / "checkpoints" / "already_indexed.safetensors"
    _touch(path, size=48)

    _index_model(hash_cache_db, path, size=48, sha256="f" * 64)

    with patch("src.features.backends.native_model_scan._hash_file") as hash_spy:
        found = scan_native_models(str(tmp_path))
        hash_spy.assert_not_called()

    assert found[0].sha256 == "f" * 64


# --- hash cache: a same-size swap must not be answered from a stale record -------
#
# The reuse tier above is the one that can hand back a digest nothing verified. Two
# records feed it - `model_hash_cache` and `models.sha256` - and a file replaced in
# place with same-size bytes moves neither on its own. These pin that the scan
# rehashes instead, and (crucially) that a wrong digest cannot be written back into
# the cache, where it would be reported by every later scan without ever being
# re-derived.

def _index_model(database, path, size, sha256, indexed_at=None):
    """A `models` row for `path`, as the depot-wide indexer would leave it.

    `indexed_at` (naive UTC, as SQLite's CURRENT_TIMESTAMP writes it) is set
    explicitly where the test needs the row to predate or postdate the file.
    """
    from src.features.models.records import Model

    model = model_repository_module.model_repo.create(Model(
        filename=path.name,
        file_path=str(path),
        file_size=size,
        sha256=sha256,
        model_type="checkpoint",
    ))
    if indexed_at is not None:
        with database.get_cursor() as cursor:
            cursor.execute(
                "UPDATE models SET indexed_at = ? WHERE id = ?",
                (indexed_at.strftime("%Y-%m-%d %H:%M:%S"), model.id),
            )
    return model


def _swap_contents(path, content, mtime_ns):
    path.write_bytes(content)
    os.utime(path, ns=(mtime_ns, mtime_ns))


def test_a_models_row_older_than_the_file_is_not_trusted(tmp_path, hash_cache_db):
    """The row was written before the file's current bytes existed, so its digest
    describes something else. Same size proves nothing - that is the whole shape of
    an in-place swap."""
    import hashlib
    from datetime import datetime, timedelta, timezone

    path = tmp_path / "checkpoints" / "swapped_before_first_scan.safetensors"
    _touch(path, size=32)

    _index_model(
        hash_cache_db, path, size=32, sha256="f" * 64,
        indexed_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
    )

    found = scan_native_models(str(tmp_path))

    assert found[0].sha256 == hashlib.sha256(b"\0" * 32).hexdigest()


def test_a_same_size_swap_is_not_answered_from_the_models_row(tmp_path, hash_cache_db):
    """A file already known to the cache, replaced with same-size bytes carrying an
    older mtime (what `rsync -t`/`cp -p` from an older copy leaves behind). The
    cache row no longer matches, which is proof the bytes moved - the `models` row
    must not be used to paper over that."""
    import hashlib
    from datetime import datetime, timezone

    path = tmp_path / "checkpoints" / "swapped.safetensors"
    _touch(path, size=16)

    first = scan_native_models(str(tmp_path))[0].sha256
    assert first == hashlib.sha256(b"\0" * 16).hexdigest()

    _index_model(
        hash_cache_db, path, size=16, sha256=first,
        indexed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    # Older than the models row, so the row cannot be ruled out by its timestamp -
    # and different from what the cache holds, which is what gives the swap away.
    _swap_contents(path, b"\x01" * 16, mtime_ns=(int(time.time()) - 3600) * 1_000_000_000)

    second = scan_native_models(str(tmp_path))[0].sha256

    assert second == hashlib.sha256(b"\x01" * 16).hexdigest()
    assert second != first


def test_the_cache_is_not_left_holding_the_pre_swap_digest(tmp_path, hash_cache_db):
    """The permanence half. Whatever the swap scan returned, the cache must now hold
    the digest of the bytes on disk: a stale digest written back under the new mtime
    turns every later scan into a cache hit that reports it forever."""
    import hashlib
    from datetime import datetime, timezone

    path = tmp_path / "checkpoints" / "swapped_twice.safetensors"
    _touch(path, size=16)
    first = scan_native_models(str(tmp_path))[0].sha256

    _index_model(
        hash_cache_db, path, size=16, sha256=first,
        indexed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    _swap_contents(path, b"\x02" * 16, mtime_ns=(int(time.time()) - 3600) * 1_000_000_000)
    scan_native_models(str(tmp_path))

    with patch("src.features.backends.native_model_scan._hash_file") as hash_spy:
        from_cache = scan_native_models(str(tmp_path))[0].sha256
        hash_spy.assert_not_called()

    assert from_cache == hashlib.sha256(b"\x02" * 16).hexdigest()
