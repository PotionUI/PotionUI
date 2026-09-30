from unittest.mock import MagicMock

import pytest

from src.features.models.catalog import ModelCatalog
from src.platform.filesystem.model_roots import NoWriteRootError
from src.platform.security.user import AccountType


def _catalog(matched=None):
    scanner = MagicMock()
    scanner.resolver.write_dir.side_effect = NoWriteRootError("x")
    scanner.MODEL_TYPE_MAPPING = {}
    repository = MagicMock()
    repository.count_by_type.return_value = {"lora": 2, "undefined": 3}
    repository.get_total_size_by_type.return_value = {}
    repository.get_available_model_ids_for_user.return_value = ["a"]
    repository.count_filtered_by_type.return_value = matched or {}
    return ModelCatalog(repository, MagicMock(), scanner, user_attribute_repository=MagicMock())


def test_only_an_unscoped_admin_sees_the_undefined_type():
    catalog = _catalog()
    admin = MagicMock(account_type=AccountType.ADMIN, id="admin")

    result = catalog.get_model_types(admin)

    assert {t["type"] for t in result["types"]} == {"lora", "undefined"}
    assert result["total"] == 5


@pytest.mark.parametrize("scoped", [True, False])
def test_everyone_else_never_sees_it(scoped):
    catalog = _catalog()
    if scoped:
        user = MagicMock(account_type=AccountType.ADMIN, id="admin")
        result = catalog.get_model_types(user, user_scoped=True)
    else:
        user = MagicMock(account_type=AccountType.USER, id="u")
        result = catalog.get_model_types(user)

    assert {t["type"] for t in result["types"]} == {"lora"}
    assert result["total"] == 2


def test_faceted_counts_never_include_undefined_for_other_users():
    catalog = _catalog({"lora": 1, "undefined": 2})
    facets = MagicMock(search_filter=None, model_type=None)
    user = MagicMock(account_type=AccountType.USER, id="u")

    result = catalog.get_model_types(user, facets=facets)

    assert result["total"] == 1
    assert {t["type"] for t in result["types"]} == {"lora"}
