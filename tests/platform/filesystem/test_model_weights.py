"""Coverage for the local-weights filesystem helpers shared by the tagger,
vision embedder, and prompt-database embedding provider."""

from src.platform.filesystem.model_weights import dir_size, weights_status


class TestDirSize:
    def test_missing_directory_returns_none(self, tmp_path):
        assert dir_size(tmp_path / "nope") is None

    def test_empty_directory_returns_none(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        assert dir_size(empty) is None

    def test_sums_bytes_across_nested_files(self, tmp_path):
        (tmp_path / "a.bin").write_bytes(b"1234")
        nested = tmp_path / "nested"
        nested.mkdir()
        (nested / "b.bin").write_bytes(b"12345678")
        assert dir_size(tmp_path) == 12


class TestWeightsStatus:
    def test_missing_directory_reports_absent_with_no_size(self, tmp_path):
        path = tmp_path / "missing"
        status = weights_status(path)
        assert status == {"present": False, "path": str(path), "size": None}

    def test_present_non_empty_directory_reports_size(self, tmp_path):
        (tmp_path / "weights.bin").write_bytes(b"12345")
        status = weights_status(tmp_path)
        assert status["present"] is True
        assert status["path"] == str(tmp_path)
        assert status["size"] == 5

    def test_existing_but_empty_directory_reports_absent(self, tmp_path):
        """`present` means "exists and is non-empty" - an empty directory
        counts as absent, matching a not-yet-downloaded weights folder."""
        status = weights_status(tmp_path)
        assert status["present"] is False
        assert status["size"] is None
