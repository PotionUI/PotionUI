from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.features.models.repository import model_repo
from src.features.models.type_repository import ModelTypeRepository
from src.features.models.type_manager import ModelTypeManager
from src.features.recipes.executors._artifact_lookup import find_artifact_model, find_slot_model
from tests.fixtures.model_index_fixtures import FLUX, Library, sha_of


@pytest.fixture
def lib(tmp_path, mock_db):
    lib = Library(tmp_path, scan_checkpoints=False)
    lib.path = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    lib.index()
    lib.sha = sha_of(lib.path)
    return lib


@pytest.fixture
def manager(lib):
    return ModelTypeManager(model_repo, lib.scanner.types, lib.scanner.recompute_types)


def assertion(sha):
    return ModelTypeRepository().get_assertions([sha]).get(sha)


def stored(lib):
    model = lib.model("flux-dev.safetensors")
    return model.model_type, model.type_source


def test_an_assertion_retypes_the_model_and_records_its_source(lib, manager):
    outcome = manager.assert_type(lib.sha, "diffusion_model", "download")

    assert outcome.applied is True
    assert stored(lib) == ("diffusion_model", "download")
    assert assertion(lib.sha)["source"] == "download"


@pytest.mark.parametrize(
    ("first", "second", "wins"),
    [
        ("download", "recipe", "recipe"),
        ("recipe", "admin", "admin"),
        ("download", "admin", "admin"),
        ("recipe", "download", "recipe"),
        ("admin", "recipe", "admin"),
        ("admin", "download", "admin"),
        ("admin", "admin", "admin"),
        ("recipe", "recipe", "recipe"),
    ],
)
def test_the_highest_ranked_source_wins_while_every_source_keeps_its_row(lib, manager, first, second, wins):
    manager.assert_type(lib.sha, "diffusion_model", first)

    outcome = manager.assert_type(lib.sha, "vae", second)

    assert assertion(lib.sha)["source"] == wins
    expected_type = "vae" if second == wins else "diffusion_model"
    assert stored(lib) == (expected_type, wins)
    assert outcome.applied is True
    assert set(ModelTypeRepository().get_assertion_rows(lib.sha)) == {first, second}


def test_an_equal_rank_assertion_replaces_the_type(lib, manager):
    manager.assert_type(lib.sha, "diffusion_model", "admin")
    manager.assert_type(lib.sha, "vae", "admin")
    assert stored(lib) == ("vae", "admin")


def test_reset_deletes_only_the_admin_assertion_and_resolves(lib, manager):
    manager.assert_type(lib.sha, "diffusion_model", "download")
    manager.assert_type(lib.sha, "vae", "admin")
    assert stored(lib) == ("vae", "admin")

    outcome = manager.reset_type(lib.sha)

    assert outcome.applied is True
    assert set(ModelTypeRepository().get_assertion_rows(lib.sha)) == {"download"}
    assert stored(lib) == ("diffusion_model", "download")


def test_reset_falls_back_through_the_remaining_ranks(lib, manager):
    manager.assert_type(lib.sha, "diffusion_model", "download")
    manager.assert_type(lib.sha, "lora", "recipe")
    manager.assert_type(lib.sha, "vae", "admin")

    manager.reset_type(lib.sha)
    assert stored(lib) == ("lora", "recipe")

    manager.reset_type(lib.sha, "recipe")
    assert stored(lib) == ("diffusion_model", "download")

    manager.reset_type(lib.sha, "download")
    assert stored(lib) == ("checkpoint", "folder")


def test_reset_of_a_source_that_was_never_written_changes_nothing(lib, manager):
    manager.assert_type(lib.sha, "diffusion_model", "download")

    outcome = manager.reset_type(lib.sha)

    assert outcome.applied is False
    assert stored(lib) == ("diffusion_model", "download")


def test_reset_without_an_assertion_is_a_no_op(lib, manager):
    assert manager.reset_type(lib.sha).applied is False
    assert stored(lib) == ("checkpoint", "folder")


def test_an_assertion_for_a_hash_nobody_has_is_kept_for_when_the_file_arrives(lib, manager):
    outcome = manager.assert_type("f" * 64, "vae", "recipe")

    assert outcome.applied is True
    assert outcome.model_id is None
    assert assertion("f" * 64)["model_type"] == "vae"


def test_a_colliding_assertion_is_rolled_back(lib, manager):
    from src.features.models.records import Model

    model_repo.create(Model(filename="flux-dev.safetensors", model_type="vae", sha256="9" * 64))

    outcome = manager.assert_type(lib.sha, "vae", "recipe")

    assert outcome.applied is False
    assert len(outcome.conflicts) == 1
    assert assertion(lib.sha) is None
    assert model_repo.get_by_sha256(lib.sha).model_type == "checkpoint"


def test_a_colliding_assertion_restores_the_previous_one(lib, manager):
    from src.features.models.records import Model

    manager.assert_type(lib.sha, "diffusion_model", "download")
    model_repo.create(Model(filename="flux-dev.safetensors", model_type="vae", sha256="9" * 64))

    manager.assert_type(lib.sha, "vae", "recipe")

    assert assertion(lib.sha)["model_type"] == "diffusion_model"
    assert assertion(lib.sha)["source"] == "download"


def test_assertions_and_resets_touch_no_files(lib, manager):
    with patch("src.features.models.indexer.read_header") as header_spy, patch(
        "builtins.open", side_effect=AssertionError("must not open files")
    ):
        manager.assert_type(lib.sha, "vae", "admin")
        manager.reset_type(lib.sha)

    header_spy.assert_not_called()


class RecipeArtifact:
    model_type = "diffusion_model"
    filename = "some-other-name.safetensors"
    variants = ()

    def __init__(self, sha):
        self.checksum = SimpleNamespace(value=sha)


def test_the_recipe_path_adopts_a_misfiled_row_through_a_recipe_assertion(lib, manager):
    artifact = RecipeArtifact(lib.sha)

    found = find_artifact_model(model_repo, artifact, manager)

    assert found.model_type == "diffusion_model"
    assert assertion(lib.sha)["source"] == "recipe"
    assert stored(lib) == ("diffusion_model", "recipe")


def test_the_recipe_path_through_a_slot_uses_the_same_assertion(lib, manager):
    found = find_slot_model(model_repo, RecipeArtifact(lib.sha), type_manager=manager)

    assert found.id == lib.model("flux-dev.safetensors").id
    assert assertion(lib.sha)["source"] == "recipe"


def test_the_recipe_path_never_overrides_an_admin_choice(lib, manager):
    manager.assert_type(lib.sha, "vae", "admin")

    found = find_artifact_model(model_repo, RecipeArtifact(lib.sha), manager)

    assert found is not None
    assert stored(lib) == ("vae", "admin")


def test_a_lookup_without_a_type_manager_changes_nothing(lib):
    found = find_artifact_model(model_repo, RecipeArtifact(lib.sha))

    assert found is not None
    assert stored(lib) == ("checkpoint", "folder")
    assert assertion(lib.sha) is None


def test_the_recipe_path_leaves_a_row_that_already_has_the_type_alone(lib, manager):
    artifact = RecipeArtifact(lib.sha)
    artifact.model_type = "checkpoint"

    find_artifact_model(model_repo, artifact, manager)

    assert assertion(lib.sha) is None


def test_the_collaborators_bundle_wires_the_manager_to_the_scanner(lib):
    from unittest.mock import Mock

    from src.features.models.collaborators import build_model_index_collaborators

    collaborators = build_model_index_collaborators(
        model_repo, Mock(), Mock(), Mock(), Mock(), lib.scanner.resolver
    )

    model = lib.model("flux-dev.safetensors")
    collaborators.types.set_admin_type(model.id, "vae", None)

    assert stored(lib) == ("vae", "admin")


def test_a_write_replaces_only_its_own_sources_row(lib):
    types = ModelTypeRepository()
    types.put_assertion(lib.sha, "vae", "admin", None, "2026-01-01T00:00:00")
    types.put_assertion(lib.sha, "lora", "download", None, "2026-01-02T00:00:00")
    types.put_assertion(lib.sha, "checkpoint", "download", None, "2026-01-03T00:00:00")

    rows = types.get_assertion_rows(lib.sha)

    assert {source: row["model_type"] for source, row in rows.items()} == {"admin": "vae", "download": "checkpoint"}
    assert assertion(lib.sha)["source"] == "admin"


def test_the_best_assertion_is_chosen_per_hash_in_a_batch(lib):
    types = ModelTypeRepository()
    types.put_assertion("a" * 64, "lora", "download", None, "2026-01-01T00:00:00")
    types.put_assertion("a" * 64, "vae", "recipe", None, "2026-01-01T00:00:00")
    types.put_assertion("b" * 64, "lora", "download", None, "2026-01-01T00:00:00")

    best = types.get_assertions(["a" * 64, "b" * 64, "c" * 64])

    assert {sha: row["source"] for sha, row in best.items()} == {"a" * 64: "recipe", "b" * 64: "download"}


def test_deleting_one_source_leaves_the_others(lib):
    types = ModelTypeRepository()
    types.put_assertion(lib.sha, "vae", "admin", None, "2026-01-01T00:00:00")
    types.put_assertion(lib.sha, "lora", "download", None, "2026-01-02T00:00:00")

    assert types.delete_assertion(lib.sha, "admin") is True
    assert types.delete_assertion(lib.sha, "admin") is False

    assert set(types.get_assertion_rows(lib.sha)) == {"download"}


def test_a_failing_recompute_restores_the_previous_assertion_and_reraises(lib):
    failing = ModelTypeManager(model_repo, lib.scanner.types, Mock(side_effect=RuntimeError("boom")))
    ModelTypeRepository().put_assertion(lib.sha, "diffusion_model", "download", None, "2026-01-01T00:00:00")

    with pytest.raises(RuntimeError):
        failing.assert_type(lib.sha, "vae", "recipe")

    assert assertion(lib.sha)["model_type"] == "diffusion_model"
    assert assertion(lib.sha)["source"] == "download"
    assert set(ModelTypeRepository().get_assertion_rows(lib.sha)) == {"download"}


def test_a_failing_recompute_removes_an_assertion_that_had_no_predecessor(lib):
    failing = ModelTypeManager(model_repo, lib.scanner.types, Mock(side_effect=RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        failing.assert_type(lib.sha, "vae", "admin")

    assert assertion(lib.sha) is None


def test_a_failing_recompute_on_reset_restores_the_assertion(lib):
    ModelTypeRepository().put_assertion(lib.sha, "vae", "admin", None, "2026-01-01T00:00:00")
    failing = ModelTypeManager(model_repo, lib.scanner.types, Mock(side_effect=RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        failing.reset_type(lib.sha)

    assert assertion(lib.sha)["model_type"] == "vae"
