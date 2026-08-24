"""Coverage for LLM-proposed media value validation, in particular the
containment check on the winning resolution (see media_values.py's module
docstring for why this check exists at the tool boundary)."""

from src.features.llm.tools.media_values import validate_media_value


class TestValidateMediaValue:
    def test_a_contained_existing_relative_path_is_accepted(self, tmp_path):
        storage_dir = tmp_path / "storage"
        (storage_dir / "generations").mkdir(parents=True)
        (storage_dir / "generations" / "1.png").write_bytes(b"x")

        errors = validate_media_value("source_image", "generations/1.png", str(storage_dir))

        assert errors == []

    def test_an_existing_absolute_path_outside_storage_is_rejected(self, tmp_path):
        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()
        outside = tmp_path / "outside" / "secret.png"
        outside.parent.mkdir()
        outside.write_bytes(b"x")

        errors = validate_media_value("source_image", str(outside), str(storage_dir))

        assert any("resolves outside your storage" in e for e in errors)

    def test_a_path_with_no_file_at_all_is_rejected(self, tmp_path):
        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()

        errors = validate_media_value("source_image", "generations/missing.png", str(storage_dir))

        assert any("no file exists" in e for e in errors)

    def test_no_storage_dir_skips_validation(self):
        assert validate_media_value("source_image", "/etc/passwd", None) == []

    def test_empty_value_is_always_allowed(self, tmp_path):
        assert validate_media_value("source_image", None, str(tmp_path)) == []
        assert validate_media_value("source_image", "", str(tmp_path)) == []
