from unittest.mock import MagicMock

from src.features.models.catalog import ModelCatalog
from src.platform.filesystem.model_roots import NoWriteRootError, TypeDir
from src.platform.security.user import AccountType


def _catalog(tmp_path, broken_type):
    (tmp_path / "loras" / "sub").mkdir(parents=True)
    (tmp_path / "checkpoints" / "nested").mkdir(parents=True)

    def write_dir(model_type):
        if model_type == broken_type:
            raise NoWriteRootError(model_type)
        folder = {"lora": "loras", "checkpoint": "checkpoints"}[model_type]
        return TypeDir("home", model_type, tmp_path / folder, 0, True, folder)

    scanner = MagicMock()
    scanner.resolver.write_dir.side_effect = write_dir
    repository = MagicMock()
    repository.count_by_type.return_value = {"lora": 1, "checkpoint": 2}
    repository.get_total_size_by_type.return_value = {}
    return ModelCatalog(repository, MagicMock(), scanner, user_attribute_repository=MagicMock())


def test_a_type_without_a_write_root_reports_no_directory_and_leaves_other_types_alone(tmp_path):
    catalog = _catalog(tmp_path, broken_type="lora")
    admin = MagicMock(account_type=AccountType.ADMIN, id="admin")

    result = catalog.get_model_types(admin)

    by_type = {entry["type"]: entry for entry in result["types"]}
    assert by_type["lora"]["directory"] is None
    assert by_type["lora"]["subdirectories"] == []
    assert by_type["checkpoint"]["directory"] == str(tmp_path / "checkpoints")
    assert by_type["checkpoint"]["subdirectories"] == ["nested"]
