from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.models import availability as av
from tests.fixtures.family_shape_fixtures import family_shapes
from tests.fixtures.model_header_fixtures import tensor_specs
from tests.fixtures.model_index_fixtures import FLUX, Library

FAMILIES = ("minimax_h3", "z_image", "krea2")


def registry(extracts=True):
    reg = Mock()
    reg.get_backends_for_engine.return_value = [Mock(backend_id="b1")]
    reg.engine_extracts_diffusion_model_from_checkpoint.return_value = extracts
    return reg


def listing(**kwargs):
    return av.models_for_engine("native", registry(), model_type="diffusion_model", **kwargs)


def names(entries):
    return sorted(entry["filename"] for entry in entries)


@pytest.fixture
def lib(tmp_path, mock_db):
    library = Library(tmp_path, scan_diffusion=True)
    library.put(library.diffusion, "flux-transformer.safetensors", FLUX)
    for family in FAMILIES:
        library.put(library.diffusion, f"{family}-dit.safetensors", tensor_specs(family_shapes(family)))
    library.index()
    return library


def test_without_a_family_declared_every_diffusion_model_is_offered(lib):
    assert names(listing()) == sorted(
        ["flux-transformer.safetensors", *[f"{family}-dit.safetensors" for family in FAMILIES]]
    )


@pytest.mark.parametrize("family", FAMILIES)
def test_a_slot_declaring_a_family_offers_only_that_family(lib, family):
    assert names(listing(families=[family])) == [f"{family}-dit.safetensors"]


def test_a_slot_can_declare_several_families(lib):
    assert names(listing(families=["minimax_h3", "krea2"])) == ["krea2-dit.safetensors", "minimax_h3-dit.safetensors"]


def test_a_file_in_a_folder_not_read_for_headers_is_never_hidden_by_the_family(lib):
    lib.set_scan("diffusion_model", False)
    lib.put(lib.diffusion, "mystery.safetensors", FLUX)
    lib.index()

    assert "mystery.safetensors" in names(listing(families=["minimax_h3"]))


def test_a_full_checkpoint_of_another_family_is_not_offered_to_the_slot(lib):
    prefix = "model.diffusion_model."
    shapes = {prefix + key: shape for key, shape in family_shapes("flux1").items()}
    extras = {"vae.decoder.a": (4,), "vae.decoder.b": (4,), "text_encoders.clip_l.a": (4,), "text_encoders.clip_l.b": (4,)}
    lib.put(lib.checkpoints, "flux1-all.safetensors", tensor_specs({**shapes, **extras}))
    lib.index()

    assert "flux1-all.safetensors" in names(listing())
    assert "flux1-all.safetensors" not in names(listing(families=["minimax_h3"]))


def test_a_model_the_admin_tagged_for_the_slot_stays_even_when_its_family_differs(lib):
    from src.features.models.repository import model_repo
    from src.platform.database.database import db
    from src.platform.util.ids import generate_ulid

    flux = next(m for m in model_repo.get_all(model_type="diffusion_model") if m.filename == "flux-transformer.safetensors")
    tag_id = generate_ulid()
    with db.get_cursor() as cursor:
        cursor.execute("INSERT INTO tags (id, name) VALUES (?, ?)", (tag_id, "h3-ok"))
        cursor.execute("INSERT INTO model_tags (model_id, tag_id) VALUES (?, ?)", (flux.id, tag_id))

    assert names(listing(families=["minimax_h3"], any_tag_ids=[tag_id])) == ["flux-transformer.safetensors"]
