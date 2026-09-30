from __future__ import annotations

import pytest

from src.features.models.indexer import ModelScanner
from src.features.models.locator import ModelFileUnavailable, ModelLocator
from src.features.models.native_availability_reconciler import NativeAvailabilityProjector
from src.features.models.repository import model_repo
from tests.fixtures.model_header_fixtures import write_safetensors
from tests.fixtures.model_index_fixtures import FLUX, Library, add_binding, add_root, make_resolver


@pytest.fixture
def retyped(tmp_path, mock_db):
    lib = Library(tmp_path)
    lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    lib.index()
    model = lib.model("flux-dev.safetensors")
    assert model.model_type == "diffusion_model"
    return lib, model


def test_the_native_ref_keeps_the_binding_type_of_the_location(retyped):
    lib, model = retyped
    projector = NativeAvailabilityProjector(resolver=lib.scanner.resolver, locations_repository=lib.scanner.locations)

    projection = projector._project()

    assert projection[model.id]["ref"] == "checkpoints/flux-dev.safetensors"


def test_a_retyped_model_resolves_through_the_ref_of_its_binding(retyped):
    lib, model = retyped
    locator = ModelLocator(lib.scanner.resolver, lib.scanner.locations)

    by_ref = locator.path_for_ref("checkpoints/flux-dev.safetensors")

    assert by_ref == lib.checkpoints / "flux-dev.safetensors"
    assert locator.path_for_model(model.id) == by_ref


def test_the_model_type_is_never_used_as_a_ref_directory(retyped):
    lib, _ = retyped
    locator = ModelLocator(lib.scanner.resolver, lib.scanner.locations)

    with pytest.raises(ModelFileUnavailable):
        locator.path_for_ref("diffusion_models/flux-dev.safetensors")


def test_pickers_list_the_model_under_its_content_type_only(retyped):
    _, model = retyped

    as_diffusion = model_repo.get_all(model_type="diffusion_model", include_providers=False, include_tags=False)
    as_checkpoint = model_repo.get_all(model_type="checkpoint", include_providers=False, include_tags=False)

    assert [m.id for m in as_diffusion] == [model.id]
    assert as_checkpoint == []


def test_mixed_type_copies_are_ordered_by_the_position_of_their_own_binding(tmp_path, mock_db):
    a_dir = tmp_path / "a" / "checkpoints"
    b_dir = tmp_path / "b" / "diffusion_models"
    a_dir.mkdir(parents=True)
    b_dir.mkdir(parents=True)
    content = write_safetensors(a_dir / "flux-dev.safetensors", FLUX, {"tag": "x"}).read_bytes()
    (b_dir / "flux-dev.safetensors").write_bytes(content)
    root_a = add_root("ra", tmp_path / "a")
    root_b = add_root("rb", tmp_path / "b")
    bindings = [
        add_binding("ra", "checkpoint", a_dir, 0, scan_headers=False),
        add_binding("rb", "diffusion_model", b_dir, 5, scan_headers=False),
    ]
    scanner = ModelScanner(make_resolver([root_a, root_b], bindings))
    scanner.index_models(max_workers=1)
    model = model_repo.get_by_filename("flux-dev.safetensors")[0]
    locator = ModelLocator(scanner.resolver, scanner.locations)

    views = locator.locations(model.id)
    winner = next(v for v in views if v.is_winner)

    assert {v.model_type for v in views} == {"checkpoint", "diffusion_model"}
    assert winner.root_id == "ra"
    assert locator.path_for_model(model.id) == a_dir / "flux-dev.safetensors"
    assert locator.location_summaries()[model.id]["location"]["root_id"] == "ra"
