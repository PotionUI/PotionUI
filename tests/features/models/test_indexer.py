import os
import tempfile
import hashlib
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest

from src.features.models.indexer import ModelScanner
from src.features.models.records import Model
from src.features.models.repository import model_repo


class TestModelScanner:
    def setup_method(self):
        """Setup test environment"""
        self.temp_dir = tempfile.mkdtemp()
        self.models_dir = Path(self.temp_dir)
        self.indexer = ModelScanner(str(self.models_dir))

    def teardown_method(self):
        """Clean up test environment"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_test_file(self, relative_path: str, content: bytes = b"test model content") -> str:
        """Create a test model file"""
        file_path = self.models_dir / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)
        return str(file_path)

    def _calculate_sha256(self, content: bytes) -> str:
        """Calculate SHA256 for test content"""
        return hashlib.sha256(content).hexdigest()

    def test_scan_models_directory_empty(self):
        """Test scanning empty directory"""
        result = self.indexer.scan_models_directory()
        assert result == []

    def test_scan_models_directory_with_models(self):
        """Test scanning directory with model files"""
        # Create test files
        self._create_test_file("checkpoints/model1.safetensors")
        self._create_test_file("loras/lora1.safetensors")
        self._create_test_file("embeddings/embed1.pt")
        self._create_test_file("checkpoints/subfolder/model2.ckpt")

        result = self.indexer.scan_models_directory()

        assert len(result) == 4
        file_paths = [item[0] for item in result]
        model_types = [item[1] for item in result]

        assert any("model1.safetensors" in path for path in file_paths)
        assert any("lora1.safetensors" in path for path in file_paths)
        assert any("embed1.pt" in path for path in file_paths)
        assert any("model2.ckpt" in path for path in file_paths)

        assert "checkpoint" in model_types
        assert "lora" in model_types
        assert "embedding" in model_types

    def test_scan_models_directory_ignores_unsupported(self):
        """Test that unsupported files are ignored"""
        self._create_test_file("checkpoints/model.txt")
        self._create_test_file("checkpoints/model.json")
        self._create_test_file("checkpoints/model.safetensors")

        result = self.indexer.scan_models_directory()

        assert len(result) == 1
        assert "model.safetensors" in result[0][0]

    def test_calculate_sha256(self):
        """Test SHA256 calculation"""
        content = b"test content for hashing"
        file_path = self._create_test_file("checkpoints/test.safetensors", content)

        result = self.indexer.calculate_sha256(file_path)
        expected = self._calculate_sha256(content)

        assert result == expected

    def test_calculate_sha256_file_not_exists(self):
        """Test SHA256 calculation with non-existent file"""
        result = self.indexer.calculate_sha256("/non/existent/path")
        assert result is None

    @patch('src.features.models.indexer.model_repo')
    def test_index_single_model_new_model(self, mock_repo):
        """Test indexing a completely new model"""
        # Setup mocks
        mock_repo.get_by_sha256.return_value = None
        mock_repo.get_by_file_path.return_value = None
        mock_repo.get_by_identity.return_value = None

        mock_model = Mock(spec=Model)
        mock_model.to_dict.return_value = {"id": "test-id", "filename": "test.safetensors"}
        mock_repo.create.return_value = mock_model

        # Create test file
        content = b"new model content"
        file_path = self._create_test_file("checkpoints/new_model.safetensors", content)

        result = self.indexer.index_single_model(file_path, "checkpoint", 1024)

        assert result == mock_model
        mock_repo.create.assert_called_once()
        mock_repo.get_by_sha256.assert_called_once()
        mock_repo.get_by_file_path.assert_called_once()

    @patch('src.features.models.indexer.model_repo')
    def test_index_single_model_duplicate_sha256_different_path(self, mock_repo):
        """Test indexing model with same SHA256 but different path"""
        # Setup existing model with different path
        existing_model = Mock(spec=Model)
        existing_model.id = "existing-id"
        existing_model.file_path = "/old/path/model.safetensors"
        existing_model.filename = "old_model.safetensors"

        mock_repo.get_by_sha256.return_value = existing_model
        mock_repo.get_by_file_path.return_value = None
        mock_repo.update.return_value = True

        # Create test file
        content = b"duplicate model content"
        file_path = self._create_test_file("checkpoints/duplicate_model.safetensors", content)

        result = self.indexer.index_single_model(file_path, "checkpoint", 1024)

        assert result == existing_model
        assert existing_model.file_path == file_path
        assert existing_model.filename == "duplicate_model.safetensors"
        mock_repo.update.assert_called_once_with(existing_model)

    @patch('src.features.models.indexer.model_repo')
    def test_index_single_model_same_path_same_hash(self, mock_repo):
        """Test indexing model that already exists at same path"""
        file_path = self._create_test_file("checkpoints/existing.safetensors")

        # Setup existing model at same path
        existing_model = Mock(spec=Model)
        existing_model.file_path = file_path
        existing_model.id = "existing-id"

        mock_repo.get_by_sha256.return_value = existing_model
        mock_repo.get_by_file_path.return_value = existing_model
        mock_repo.update.return_value = True

        result = self.indexer.index_single_model(file_path, "checkpoint", 1024)

        assert result == existing_model
        mock_repo.update.assert_called_once_with(existing_model)

    @patch('src.features.models.indexer.model_repo')
    def test_index_models_only_new_files(self, mock_repo):
        """Test that index_models only processes new files"""
        # Create test files
        file1 = self._create_test_file("checkpoints/model1.safetensors")
        file2 = self._create_test_file("checkpoints/model2.safetensors")
        file3 = self._create_test_file("loras/lora1.safetensors")

        # Setup existing models in database (file1 and file2 already indexed)
        existing_model1 = Mock(spec=Model)
        existing_model1.file_path = file1
        existing_model2 = Mock(spec=Model)
        existing_model2.file_path = file2

        mock_repo.get_all.return_value = [existing_model1, existing_model2]

        # Mock the indexing of the new file
        new_model = Mock(spec=Model)
        new_model.to_dict.return_value = {"id": "new-id", "filename": "lora1.safetensors"}

        with patch.object(self.indexer, 'index_single_model') as mock_index:
            mock_index.return_value = new_model

            result = self.indexer.index_models()

            # Should only call index_single_model for the new file (file3)
            mock_index.assert_called_once()
            call_args = mock_index.call_args[0]
            assert call_args[0] == file3  # file_path
            assert call_args[1] == "lora"  # model_type

            # Check result statistics
            assert result['indexed'] == 1
            assert result['skipped'] == 2
            assert result['total'] == 3
            assert result['new_files'] == 1

    @patch('src.features.models.indexer.model_repo')
    def test_index_models_reports_progress_per_file(self, mock_repo):
        """A large models folder makes indexing take a while
        (`index_single_model` hashes every new file's full contents) - the
        setup-run `models.index` step (see
        src/features/setup/executors/models_index.py) needs a "N of M" tick
        per completed file, not just the indeterminate "Scanning..." one at
        the very start."""
        file1 = self._create_test_file("checkpoints/model1.safetensors")
        file2 = self._create_test_file("checkpoints/model2.safetensors")
        mock_repo.get_all.return_value = []  # both files are new

        new_model = Mock(spec=Model)
        new_model.to_dict.return_value = {"id": "new-id", "filename": "model1.safetensors"}

        ticks = []
        self.indexer.set_progress_callback(lambda current, total, message: ticks.append((current, total)))

        with patch.object(self.indexer, 'index_single_model', return_value=new_model):
            self.indexer.index_models()

        # (0, 0) first - the indeterminate "Scanning..." tick before the file
        # count is even known - then a "0 of N" tick as soon as it is, then
        # exactly one tick per completed file (all against that same total).
        assert ticks[0] == (0, 0)
        assert ticks[1] == (0, 2)
        assert {t for t, _ in ticks[2:]} == {1, 2}
        assert all(total == 2 for _, total in ticks[1:])

    @patch('src.features.models.indexer.model_repo')
    def test_index_models_no_new_files(self, mock_repo):
        """Test index_models when all files are already indexed"""
        # Create test files
        file1 = self._create_test_file("checkpoints/model1.safetensors")
        file2 = self._create_test_file("checkpoints/model2.safetensors")

        # Setup existing models (all files already indexed)
        existing_model1 = Mock(spec=Model)
        existing_model1.file_path = file1
        existing_model2 = Mock(spec=Model)
        existing_model2.file_path = file2

        mock_repo.get_all.return_value = [existing_model1, existing_model2]

        with patch.object(self.indexer, '_cleanup_deleted_models'):
            result = self.indexer.index_models()

            assert result['indexed'] == 0
            assert result['skipped'] == 2
            assert result['total'] == 2
            assert result['new_files'] == 0

    @patch('src.features.models.indexer.model_repo')
    def test_index_models_empty_directory(self, mock_repo):
        """Test index_models with empty directory"""
        mock_repo.get_all.return_value = []

        result = self.indexer.index_models()

        assert result['indexed'] == 0
        assert result['skipped'] == 0
        assert result['total'] == 0
        assert result['new_files'] == 0

    @patch('src.features.models.indexer.model_repo')
    def test_cleanup_deleted_models(self, mock_repo):
        """A model whose file has gone missing is soft-marked unavailable, not deleted -
        the models location can be switched away and back (src.features.models.location),
        and a hard delete would throw away its tags/ratings/assignments."""
        # Create one existing file
        existing_file = self._create_test_file("checkpoints/existing.safetensors")

        # Setup mock models - one exists, one doesn't
        existing_model = Mock(spec=Model)
        existing_model.file_path = existing_file
        existing_model.id = "existing-id"
        existing_model.is_available = True

        deleted_model = Mock(spec=Model)
        deleted_model.file_path = "/non/existent/path.safetensors"
        deleted_model.id = "deleted-id"
        deleted_model.filename = "deleted.safetensors"
        deleted_model.is_available = True

        mock_repo.get_all.return_value = [existing_model, deleted_model]
        mock_repo.mark_unavailable.return_value = True

        self.indexer._cleanup_deleted_models()

        # Should only mark the non-existent model, and never delete anything
        mock_repo.mark_unavailable.assert_called_once_with("deleted-id")
        mock_repo.delete.assert_not_called()

    @patch('src.features.models.indexer.model_repo')
    def test_cleanup_skips_models_already_marked_unavailable(self, mock_repo):
        """Avoid a redundant write (and unavailable_at timestamp bump) for a row
        that was already marked unavailable by an earlier scan."""
        already_unavailable = Mock(spec=Model)
        already_unavailable.file_path = "/non/existent/path.safetensors"
        already_unavailable.id = "already-id"
        already_unavailable.is_available = False

        mock_repo.get_all.return_value = [already_unavailable]

        self.indexer._cleanup_deleted_models()

        mock_repo.mark_unavailable.assert_not_called()

    @patch('src.features.models.indexer.model_repo')
    def test_index_models_revives_a_model_whose_file_reappears(self, mock_repo):
        """A model marked unavailable is excluded from `existing_paths`, so when its
        file is found on disk again (the models location switched back), the scan
        treats it as new and routes it back through `index_single_model`."""
        revived_file = self._create_test_file("checkpoints/revived.safetensors")

        unavailable_model = Mock(spec=Model)
        unavailable_model.file_path = revived_file
        unavailable_model.is_available = False

        mock_repo.get_all.return_value = [unavailable_model]

        revived = Mock(spec=Model)
        revived.to_dict.return_value = {"id": "revived-id", "filename": "revived.safetensors"}

        with patch.object(self.indexer, 'index_single_model', return_value=revived) as mock_index:
            result = self.indexer.index_models()

        mock_index.assert_called_once()
        assert mock_index.call_args[0][0] == revived_file
        assert result['indexed'] == 1
        assert result['skipped'] == 0

    def test_get_indexing_status(self):
        """Test getting indexing status"""
        # Create test files
        self._create_test_file("checkpoints/model1.safetensors")
        self._create_test_file("loras/lora1.safetensors")

        with patch('src.features.models.indexer.model_repo') as mock_repo:
            mock_repo.count_by_type.return_value = {"checkpoint": 5, "lora": 3}
            mock_repo.get_total_size_by_type.return_value = {"checkpoint": 5000000000, "lora": 500000000}
            mock_repo.get_models_missing_hashes.return_value = []
            mock_repo.get_models_without_civitai_info.return_value = []

            status = self.indexer.get_indexing_status()

            assert status['total_models_db'] == 8
            assert status['total_size_gb'] == 5.12  # Updated to match actual calculation
            assert status['by_type']['checkpoint']['count'] == 5
            assert status['by_type']['lora']['count'] == 3

    def test_progress_callback(self):
        """Test progress callback functionality"""
        callback_calls = []

        def test_callback(current, total, message):
            callback_calls.append((current, total, message))

        self.indexer.set_progress_callback(test_callback)
        self.indexer._report_progress(5, 10, "Test message")

        assert len(callback_calls) == 1
        assert callback_calls[0] == (5, 10, "Test message")

    @patch('src.features.models.indexer.model_repo')
    def test_same_basename_under_one_type_updates_instead_of_creating(self, mock_repo):
        """`UNIQUE(model_type, filename)` (migration 074) makes a second row an
        IntegrityError that aborts the whole index run. Identity is (type, filename),
        so the existing row is updated instead."""
        existing = Model(
            id="m1", filename="x.safetensors", file_path="models/loras/styleA/x.safetensors",
            file_size=32, sha256="hash_a", model_type="lora",
        )
        mock_repo.get_by_sha256.return_value = None
        mock_repo.get_by_file_path.return_value = None
        mock_repo.get_by_identity.return_value = existing

        path = self._create_test_file("loras/styleB/x.safetensors", b"B" * 64)

        with patch.object(self.indexer, 'calculate_sha256', return_value="hash_b"):
            result = self.indexer.index_single_model(path, "lora", 64)

        mock_repo.create.assert_not_called()
        mock_repo.update.assert_called_once()
        assert result.id == "m1"
        assert result.sha256 == "hash_b"
        assert result.file_path == path

    @patch('src.features.models.indexer.SettingRepository')
    def test_reads_the_models_dir_setting_key(self, mock_repo_cls):
        """The setting key is `models_dir`; reading `model_dir` silently returns None
        and falls back to the default, which hides any configured directory."""
        setting = Mock()
        setting.get_typed_value.return_value = "/srv/weights"
        mock_repo_cls.return_value.get_setting_by_key.return_value = setting

        indexer = ModelScanner()

        mock_repo_cls.return_value.get_setting_by_key.assert_called_once_with('models_dir')
        assert indexer.models_dir == Path("/srv/weights")

    @patch('src.features.models.indexer.SettingRepository')
    def test_falls_back_to_default_when_setting_absent(self, mock_repo_cls):
        mock_repo_cls.return_value.get_setting_by_key.return_value = None

        assert ModelScanner().models_dir == Path("models")


class TestModelScannerHFDirectories:
    """`models/llm/<name>/` HF-layout checkpoint directories."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.models_dir = Path(self.temp_dir)
        self.indexer = ModelScanner(str(self.models_dir))

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_hf_checkpoint(
        self,
        name: str,
        shards: dict = None,
        config: bytes = b'{"model_type": "qwen3"}',
        under: str = "llm",
    ) -> Path:
        """Create a fake HF-layout checkpoint directory: config.json + shards."""
        shards = shards if shards is not None else {"model-00001-of-00001.safetensors": b"X" * 32}
        ckpt_dir = self.models_dir / under / name
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        (ckpt_dir / "config.json").write_bytes(config)
        for shard_name, content in shards.items():
            (ckpt_dir / shard_name).write_bytes(content)
        # Non-shard HF metadata files must not affect size or fingerprint.
        (ckpt_dir / "tokenizer_config.json").write_bytes(b"{}")
        return ckpt_dir

    def test_scan_finds_hf_directory_as_one_entry(self):
        self._create_hf_checkpoint("Qwen3-4B", shards={
            "model-00001-of-00002.safetensors": b"A" * 16,
            "model-00002-of-00002.safetensors": b"B" * 48,
        })

        result = self.indexer.scan_models_directory()

        assert len(result) == 1
        file_path, model_type, file_size = result[0]
        assert file_path.endswith("Qwen3-4B")
        assert model_type == "llm"
        assert file_size == 16 + 48  # shards only, tokenizer_config.json excluded

    def test_scan_does_not_index_shards_individually(self):
        self._create_hf_checkpoint("Qwen3-4B")

        result = self.indexer.scan_models_directory()

        paths = [p for p, _, _ in result]
        assert not any(p.endswith(".safetensors") for p in paths)
        assert len(result) == 1

    def test_scan_ignores_subdirectory_without_config_json(self):
        stray = self.models_dir / "llm" / "not-a-checkpoint"
        stray.mkdir(parents=True)
        (stray / "readme.txt").write_bytes(b"hello")

        result = self.indexer.scan_models_directory()

        assert result == []

    def test_scan_ignores_directory_with_config_but_no_shards(self):
        empty_ckpt = self.models_dir / "llm" / "config-only"
        empty_ckpt.mkdir(parents=True)
        (empty_ckpt / "config.json").write_bytes(b"{}")

        result = self.indexer.scan_models_directory()

        assert result == []

    def test_regular_file_types_still_scanned_alongside_llm_dir(self):
        self._create_hf_checkpoint("Qwen3-4B")
        self._create_test_file_in_type_dir("checkpoints/model1.safetensors")

        result = self.indexer.scan_models_directory()

        assert len(result) == 2
        model_types = {model_type for _, model_type, _ in result}
        assert model_types == {"llm", "checkpoint"}

    def _create_test_file_in_type_dir(self, relative_path: str, content: bytes = b"content") -> str:
        file_path = self.models_dir / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)
        return str(file_path)

    def test_fingerprint_is_stable_across_calls(self):
        ckpt_dir = self._create_hf_checkpoint("Qwen3-4B")

        first = self.indexer.calculate_directory_fingerprint(str(ckpt_dir))
        second = self.indexer.calculate_directory_fingerprint(str(ckpt_dir))

        assert first is not None
        assert first == second
        assert len(first) == 64  # hex sha256 digest, so downstream sha256-shaped checks pass

    def test_fingerprint_changes_when_shard_set_changes(self):
        ckpt_dir = self._create_hf_checkpoint("Qwen3-4B")
        before = self.indexer.calculate_directory_fingerprint(str(ckpt_dir))

        (ckpt_dir / "model-00002-of-00002.safetensors").write_bytes(b"extra shard")

        after = self.indexer.calculate_directory_fingerprint(str(ckpt_dir))

        assert before != after

    def test_fingerprint_changes_when_shard_resized(self):
        ckpt_dir = self._create_hf_checkpoint(
            "Qwen3-4B", shards={"model-00001-of-00001.safetensors": b"X" * 32}
        )
        before = self.indexer.calculate_directory_fingerprint(str(ckpt_dir))

        (ckpt_dir / "model-00001-of-00001.safetensors").write_bytes(b"X" * 64)

        after = self.indexer.calculate_directory_fingerprint(str(ckpt_dir))

        assert before != after

    @patch('src.features.models.indexer.model_repo')
    def test_index_single_model_new_hf_directory(self, mock_repo):
        mock_repo.get_by_sha256.return_value = None
        mock_repo.get_by_file_path.return_value = None
        mock_repo.get_by_identity.return_value = None

        mock_model = Mock(spec=Model)
        mock_repo.create.return_value = mock_model

        ckpt_dir = self._create_hf_checkpoint("Qwen3-4B")

        result = self.indexer.index_single_model(str(ckpt_dir), "llm", 32)

        assert result == mock_model
        created_model = mock_repo.create.call_args[0][0]
        assert created_model.is_directory is True
        assert created_model.filename == "Qwen3-4B"
        assert created_model.model_type == "llm"
        assert len(created_model.sha256) == 64

    @patch('src.features.models.indexer.model_repo')
    def test_index_single_model_hf_directory_reindex_is_idempotent(self, mock_repo):
        """Re-indexing the same, unchanged directory updates the existing row
        rather than creating a duplicate (same fingerprint -> same sha256 match)."""
        ckpt_dir = self._create_hf_checkpoint("Qwen3-4B")
        fingerprint = self.indexer.calculate_directory_fingerprint(str(ckpt_dir))

        existing = Model(
            id="m1", filename="Qwen3-4B", file_path=str(ckpt_dir),
            file_size=32, sha256=fingerprint, model_type="llm", is_directory=True,
        )
        mock_repo.get_by_sha256.return_value = existing
        mock_repo.get_by_file_path.return_value = existing

        result = self.indexer.index_single_model(str(ckpt_dir), "llm", 32)

        mock_repo.create.assert_not_called()
        mock_repo.update.assert_called_once_with(existing)
        assert result.id == "m1"

    @patch('src.features.models.indexer.model_repo')
    def test_index_single_model_single_file_still_uses_real_sha256(self, mock_repo):
        """Regression guard: a plain file (not a directory) must keep hashing its
        real bytes, never the directory-fingerprint path."""
        mock_repo.get_by_sha256.return_value = None
        mock_repo.get_by_file_path.return_value = None
        mock_repo.get_by_identity.return_value = None
        mock_model = Mock(spec=Model)
        mock_repo.create.return_value = mock_model

        content = b"plain checkpoint bytes"
        file_path = self._create_test_file_in_type_dir("checkpoints/model.safetensors", content)

        self.indexer.index_single_model(file_path, "checkpoint", len(content))

        created_model = mock_repo.create.call_args[0][0]
        assert created_model.is_directory is False
        assert created_model.sha256 == hashlib.sha256(content).hexdigest()

class TestHashCacheSeeding:
    """What `index_single_model` writes into `model_hash_cache`.

    The cache answers a later `scan_native_models` without re-reading the file, so
    the key it is stored under has to describe the bytes that were actually hashed.
    Stat'ing the file after hashing files the digest under whatever the file looks
    like once the hash finishes - and a file being rewritten while it is hashed then
    gets a digest of the old bytes stored under the new file's size and mtime, which
    every later scan reports as a fresh cache hit.
    """

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.models_dir = Path(self.temp_dir)
        self.indexer = ModelScanner(str(self.models_dir))

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _file(self, name="checkpoints/model.safetensors", content=b"original bytes"):
        path = self.models_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _index(self, path, size, hash_side_effect=None):
        """Index one file, returning the calls made against the hash cache."""
        cache = Mock()
        with patch('src.features.models.indexer.model_repo') as repo, \
             patch('src.features.models.hash_cache_repository.model_hash_cache_repo', cache):
            repo.get_by_sha256.return_value = None
            repo.get_by_file_path.return_value = None
            repo.get_by_identity.return_value = None
            repo.create.return_value = Mock(spec=Model)

            if hash_side_effect is None:
                self.indexer.index_single_model(str(path), "checkpoint", size)
            else:
                with patch.object(
                    ModelScanner, 'calculate_sha256', side_effect=hash_side_effect
                ):
                    self.indexer.index_single_model(str(path), "checkpoint", size)
        return cache

    def test_an_untouched_file_is_cached_under_its_own_size_and_mtime(self):
        content = b"original bytes"
        path = self._file(content=content)
        stat = path.stat()

        cache = self._index(path, len(content))

        cache.put.assert_called_once_with(
            str(path), stat.st_size, stat.st_mtime_ns, hashlib.sha256(content).hexdigest()
        )

    def test_a_file_rewritten_mid_hash_is_not_cached_under_its_new_mtime(self):
        """The digest belongs to the bytes that were read, so it must be filed under
        the state they were read from. Anything else is a cache entry that claims to
        describe the file as it is now."""
        path = self._file(content=b"original bytes")
        before = path.stat().st_mtime_ns

        def rewrite_then_hash(file_path, chunk_size=8192):
            os.utime(path, ns=(before + 5_000_000_000, before + 5_000_000_000))
            return "a" * 64

        cache = self._index(path, 14, hash_side_effect=rewrite_then_hash)

        cached_mtime = cache.put.call_args[0][2]
        assert cached_mtime == before
        assert cached_mtime != path.stat().st_mtime_ns

    def test_a_file_that_cannot_be_stat_is_not_cached_at_all(self):
        """No trustworthy key, no entry - a digest under a guessed key is worse than
        no cache row."""
        path = self._file(content=b"original bytes")

        def unlink_then_hash(file_path, chunk_size=8192):
            return "b" * 64

        with patch.object(ModelScanner, '_file_identity', return_value=None):
            cache = self._index(path, 14, hash_side_effect=unlink_then_hash)

        cache.put.assert_not_called()
