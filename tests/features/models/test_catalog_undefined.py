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


@pytest.fixture
def library_catalog(tmp_path, mock_db):
    from src.features.models.access_policy import ModelAccessPolicy
    from src.features.models.locator import ModelLocator
    from src.features.models.repository import model_repo
    from tests.fixtures.model_index_fixtures import FLUX, LORA, Library

    with mock_db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, account_type) "
            "VALUES ('u1', 'user', 'user@example.test', 'x', 'USER')"
        )
    lib = Library(tmp_path)
    lib.put(lib.checkpoints, "flux.safetensors", FLUX)
    lib.put(lib.checkpoints, "mystery.safetensors", LORA)
    lib.index()
    for model in model_repo.get_all(include_providers=False, include_tags=False, include_undefined=True):
        model_repo.assign_model_to_user(model.id, "u1")
    catalog = ModelCatalog(
        model_repo, ModelAccessPolicy(model_repo), lib.scanner,
        user_attribute_repository=MagicMock(get_maps=MagicMock(return_value={})),
        locator=ModelLocator(lib.scanner.resolver, lib.scanner.locations),
    )
    return catalog


def names(result):
    return {m["filename"] for m in result["models"]}


def test_the_unscoped_admin_library_lists_and_counts_undefined_models(library_catalog):
    from src.features.models.catalog import ListModelsParams

    admin = MagicMock(account_type=AccountType.ADMIN, id="admin")

    result = library_catalog.list_models(ListModelsParams(all_models=True), admin)

    assert names(result) == {"flux.safetensors", "mystery.safetensors"}
    assert result["total"] == 2


def test_the_admin_can_filter_the_library_to_undefined_models(library_catalog):
    from src.features.models.catalog import ListModelsParams

    admin = MagicMock(account_type=AccountType.ADMIN, id="admin")

    result = library_catalog.list_models(ListModelsParams(all_models=True, model_type="undefined"), admin)

    assert names(result) == {"mystery.safetensors"}
    assert result["total"] == 1


def test_a_scoped_admin_listing_excludes_undefined_models(library_catalog):
    from src.features.models.catalog import ListModelsParams

    admin = MagicMock(account_type=AccountType.ADMIN, id="u1")

    result = library_catalog.list_models(ListModelsParams(all_models=False), admin)

    assert names(result) == {"flux.safetensors"}
    assert result["total"] == 1


def test_a_regular_user_never_sees_undefined_models_even_when_assigned(library_catalog):
    from src.features.models.catalog import ListModelsParams

    user = MagicMock(account_type=AccountType.USER, id="u1")

    result = library_catalog.list_models(ListModelsParams(all_models=True), user)

    assert names(result) == {"flux.safetensors"}
    assert result["total"] == 1


def test_the_admin_type_facets_and_tag_counts_include_undefined(library_catalog):
    admin = MagicMock(account_type=AccountType.ADMIN, id="admin")
    facets = MagicMock(
        search_filter=None, model_type=None, tag_ids=None, search=None, assignment_filter=None,
        assigned_user_id=None, assigned_group_id=None, favorites_only=False, collection_id=None,
        in_any_collection=False,
    )

    result = library_catalog.get_model_types(admin, facets=facets)

    counts = {t["type"]: t["count"] for t in result["types"]}
    assert counts["undefined"] == 1
    assert result["total"] == 2
