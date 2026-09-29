import hashlib
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
import pytest

from src.features.models.indexer import FoundFile, IndexOutcome, ModelScanner
from src.features.models.records import Model


def _scanner() -> ModelScanner:
    return ModelScanner(Mock())


class TestHashing:

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.dir = Path(self.temp_dir)
        self.scanner = _scanner()

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _file(self, name: str, content: bytes) -> str:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path)

    def test_calculate_sha256(self):
        content = b"test content for hashing"
        file_path = self._file("model.safetensors", content)

        assert self.scanner.calculate_sha256(file_path) == hashlib.sha256(content).hexdigest()

    def test_calculate_sha256_file_not_exists(self):
        assert self.scanner.calculate_sha256("/non/existent/path") is None

    def test_calculate_sha256_matches_across_chunk_boundaries(self):
        """A file spanning several read chunks must hash identically to hashlib
        run over the whole buffer at once - the default `chunk_size` (4 MiB, raised
        from 8192 bytes to cut GIL contention during indexing) must not change the
        digest for a file several chunks long."""
        import random

        rng = random.Random(1234)
        content = rng.randbytes(10 * 1024 * 1024)
        file_path = self._file("large.safetensors", content)

        assert self.scanner.calculate_sha256(file_path) == hashlib.sha256(content).hexdigest()

    def test_calculate_sha256_cancel_check_raises_scan_cancelled(self):
        from src.features.models.indexer import ScanCancelled

        content = b"x" * (8 * 1024 * 1024)
        file_path = self._file("big.safetensors", content)
        calls = {"n": 0}

        def cancel_after_first_chunk():
            calls["n"] += 1
            return calls["n"] > 1

        with pytest.raises(ScanCancelled):
            self.scanner.calculate_sha256(file_path, cancel_check=cancel_after_first_chunk)


class TestHfDirectoryFingerprint:

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.dir = Path(self.temp_dir)
        self.scanner = _scanner()

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _checkpoint(self, name: str, shards=None, config: bytes = b'{"model_type": "qwen3"}') -> Path:
        shards = shards if shards is not None else {"model-00001-of-00001.safetensors": b"X" * 32}
        ckpt_dir = self.dir / name
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        (ckpt_dir / "config.json").write_bytes(config)
        for shard_name, content in shards.items():
            (ckpt_dir / shard_name).write_bytes(content)
        (ckpt_dir / "tokenizer_config.json").write_bytes(b"{}")
        return ckpt_dir

    def test_fingerprint_is_stable_across_calls(self):
        ckpt_dir = self._checkpoint("Qwen3-4B")

        first = self.scanner.calculate_directory_fingerprint(str(ckpt_dir))
        second = self.scanner.calculate_directory_fingerprint(str(ckpt_dir))

        assert first is not None
        assert first == second
        assert len(first) == 64

    def test_fingerprint_changes_when_shard_set_changes(self):
        ckpt_dir = self._checkpoint("Qwen3-4B")
        before = self.scanner.calculate_directory_fingerprint(str(ckpt_dir))

        (ckpt_dir / "model-00002-of-00002.safetensors").write_bytes(b"extra shard")

        after = self.scanner.calculate_directory_fingerprint(str(ckpt_dir))

        assert before != after

    def test_fingerprint_changes_when_shard_resized(self):
        ckpt_dir = self._checkpoint("Qwen3-4B", shards={"model-00001-of-00001.safetensors": b"X" * 32})
        before = self.scanner.calculate_directory_fingerprint(str(ckpt_dir))

        (ckpt_dir / "model-00001-of-00001.safetensors").write_bytes(b"X" * 64)

        after = self.scanner.calculate_directory_fingerprint(str(ckpt_dir))

        assert before != after

    def test_fingerprint_excludes_non_shard_metadata_files(self):
        ckpt_dir = self._checkpoint("Qwen3-4B", shards={"model-00001-of-00001.safetensors": b"X" * 16})
        before = self.scanner.calculate_directory_fingerprint(str(ckpt_dir))

        (ckpt_dir / "tokenizer_config.json").write_bytes(b'{"changed": true}')

        after = self.scanner.calculate_directory_fingerprint(str(ckpt_dir))

        assert before == after


class TestHashCacheReuse:

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.dir = Path(self.temp_dir)
        self.scanner = _scanner()

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _file(self, content: bytes = b"original bytes") -> Path:
        path = self.dir / "model.safetensors"
        path.write_bytes(content)
        return path

    def test_a_cache_hit_at_the_same_size_and_mtime_skips_hashing(self):
        path = self._file()
        stat = path.stat()
        cache = Mock()
        cache.get.return_value = Mock(size=stat.st_size, mtime_ns=stat.st_mtime_ns, sha256="cached-digest")

        with patch(
            "src.features.models.hash_cache_repository.model_hash_cache_repo", cache
        ), patch.object(ModelScanner, "calculate_sha256") as hash_spy:
            digest = self.scanner._digest_for(str(path), stat.st_size, stat.st_mtime_ns)

        hash_spy.assert_not_called()
        assert digest == "cached-digest"

    def test_a_cache_miss_hashes_and_seeds_the_cache_under_the_scanned_identity(self):
        content = b"original bytes"
        path = self._file(content)
        stat = path.stat()
        cache = Mock()
        cache.get.return_value = None

        with patch("src.features.models.hash_cache_repository.model_hash_cache_repo", cache):
            digest = self.scanner._digest_for(str(path), stat.st_size, stat.st_mtime_ns)

        assert digest == hashlib.sha256(content).hexdigest()
        cache.put.assert_called_once_with(str(path), stat.st_size, stat.st_mtime_ns, digest)


class TestIndexSingleModelCompatWrapper:

    def test_resolves_the_path_through_the_resolver_and_delegates_to_index_file(self, tmp_path):
        from src.platform.filesystem.model_roots import LogicalLocation

        path = tmp_path / "checkpoints" / "m.safetensors"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"weights")

        resolver = Mock()
        resolver.to_logical.return_value = LogicalLocation(root_id="home", model_type="checkpoint", rel_path="m.safetensors")
        scanner = ModelScanner(resolver)
        recorded = {}

        def fake_index_file(found, cancel_check=None):
            recorded["found"] = found
            return IndexOutcome(Mock(spec=Model), set())

        scanner.index_file = fake_index_file

        result = scanner.index_single_model(str(path), "checkpoint", 7)

        assert result.model is not None
        found = recorded["found"]
        assert isinstance(found, FoundFile)
        assert found.root_id == "home"
        assert found.model_type == "checkpoint"
        assert found.rel_path == "m.safetensors"
        assert found.size == 7

    def test_a_path_outside_every_root_is_refused_without_raising(self, tmp_path):
        resolver = Mock()
        resolver.to_logical.return_value = None
        scanner = ModelScanner(resolver)

        result = scanner.index_single_model(str(tmp_path / "nowhere.safetensors"), "checkpoint", 1)

        assert result.model is None
        assert result.duplicate_of is None
