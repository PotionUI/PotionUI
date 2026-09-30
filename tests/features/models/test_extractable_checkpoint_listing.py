from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from src.features.models import availability as av
from src.features.models import repository as repository_module
from src.features.models.form_refs import make_model_ref, resolve_form_model_refs
from src.features.models.locator import ModelLocator
from src.features.models.repository import model_repo
from src.features.models.type_repository import verdict_is_current
from tests.fixtures.family_shape_fixtures import family_shapes
from tests.fixtures.model_header_fixtures import tensor_specs, write_gguf
from tests.fixtures.model_index_fixtures import FLUX, SDXL_ALL_IN_ONE, Library

VAE = {"vae.decoder.a": (4,), "vae.decoder.b": (4,)}
TE = {"text_encoders.clip_l.a": (4,), "text_encoders.clip_l.b": (4,)}
PREFIX = "model.diffusion_model."


def all_in_one(family="flux1", extra=None):
    shapes = {PREFIX + key: shape for key, shape in family_shapes(family).items()}
    return tensor_specs({**shapes, **VAE, **TE, **(extra or {})})


def bnb_all_in_one():
    specs = all_in_one()
    specs[PREFIX + "double_blocks.0.img_attn.qkv.weight"] = ("U8", (8, 1))
    specs[PREFIX + "double_blocks.0.img_attn.qkv.weight.absmax"] = ("F32", (4,))
    specs[PREFIX + "double_blocks.0.img_attn.qkv.weight.quant_map"] = ("F32", (16,))
    specs[PREFIX + "double_blocks.0.img_attn.qkv.weight.quant_state.bitsandbytes__nf4"] = ("U8", (32,))
    return specs


def registry(extracts):
    reg = Mock()
    backend = Mock(backend_id="b1")
    reg.get_backends_for_engine.return_value = [backend]
    reg.engine_extracts_diffusion_model_from_checkpoint.return_value = extracts
    return reg


def listing(engine="native", extracts=True, model_type="diffusion_model"):
    return av.models_for_engine(engine, registry(extracts), model_type=model_type)


def names(entries):
    return sorted(entry["filename"] for entry in entries)


@pytest.fixture
def lib(tmp_path, mock_db):
    return Library(tmp_path)


def test_native_diffusion_model_picker_lists_an_extractable_flux_all_in_one(lib):
    lib.put(lib.checkpoints, "flux1-dev-fp8.safetensors", all_in_one())
    lib.put(lib.diffusion, "flux-transformer.safetensors", FLUX)
    lib.index()

    entries = listing()

    assert names(entries) == ["flux-transformer.safetensors", "flux1-dev-fp8.safetensors"]
    by_name = {entry["filename"]: entry for entry in entries}
    assert by_name["flux1-dev-fp8.safetensors"]["model_type"] == "checkpoint"
    assert by_name["flux1-dev-fp8.safetensors"]["packaging"] == "full_checkpoint"
    assert "packaging" not in by_name["flux-transformer.safetensors"]


def test_the_stored_type_stays_checkpoint(lib):
    lib.put(lib.checkpoints, "flux1-dev-fp8.safetensors", all_in_one())
    lib.index()

    assert lib.model("flux1-dev-fp8.safetensors").model_type == "checkpoint"
    assert [e["filename"] for e in listing(model_type="checkpoint")] == ["flux1-dev-fp8.safetensors"]


def test_an_engine_without_the_capability_lists_exact_types_only(lib):
    lib.put(lib.checkpoints, "flux1-dev-fp8.safetensors", all_in_one())
    lib.put(lib.diffusion, "flux-transformer.safetensors", FLUX)
    lib.index()

    assert names(listing(engine="comfyui", extracts=False)) == ["flux-transformer.safetensors"]


def test_only_the_diffusion_model_picker_gets_the_secondary_listing(lib):
    lib.put(lib.checkpoints, "flux1-dev-fp8.safetensors", all_in_one())
    lib.index()

    assert listing(model_type="lora") == []
    assert all("packaging" not in entry for entry in listing(model_type="checkpoint"))


def test_an_sdxl_checkpoint_is_not_listed(lib):
    lib.put(lib.checkpoints, "sdxl.safetensors", SDXL_ALL_IN_ONE)
    lib.index()

    assert lib.model("sdxl.safetensors").model_type == "checkpoint"
    assert listing() == []


def test_a_bnb_nf4_all_in_one_is_not_listed(lib):
    lib.put(lib.checkpoints, "flux1-dev-bnb-nf4.safetensors", bnb_all_in_one())
    lib.index()

    assert lib.model("flux1-dev-bnb-nf4.safetensors").model_type == "checkpoint"
    assert listing() == []


def test_a_gguf_all_in_one_is_not_listed(lib):
    shapes = {PREFIX + key: shape for key, shape in family_shapes("flux1").items()}
    write_gguf(lib.checkpoints / "flux-all.gguf", tensor_specs({**shapes, **VAE, **TE}))
    lib.index()

    assert lib.model("flux-all.gguf").model_type == "checkpoint"
    assert listing() == []


def test_a_family_outside_the_allowlist_is_not_listed(lib):
    lib.put(lib.checkpoints, "ltx-all.safetensors", all_in_one("ltxv"))
    lib.index()

    assert lib.model("ltx-all.safetensors").model_type == "checkpoint"
    assert listing() == []


def test_the_query_gates_families_by_the_allowlist(lib):
    lib.put(lib.checkpoints, "flux1-dev-fp8.safetensors", all_in_one())
    lib.put(lib.checkpoints, "qwen-all.safetensors", all_in_one("qwen_image"))
    lib.index()
    assert names(listing()) == ["flux1-dev-fp8.safetensors", "qwen-all.safetensors"]

    with patch.object(repository_module, "TRANSFORMER_EXTRACTABLE_FAMILIES", frozenset({"qwen_image"})):
        assert names(listing()) == ["qwen-all.safetensors"]


def test_the_search_and_the_secondary_listing_combine(lib):
    lib.put(lib.checkpoints, "flux1-dev-fp8.safetensors", all_in_one())
    lib.put(lib.checkpoints, "qwen-all.safetensors", all_in_one("qwen_image"))
    lib.index()

    found = av.models_for_engine("native", registry(True), model_type="diffusion_model", search="qwen")

    assert names(found) == ["qwen-all.safetensors"]


def test_the_tag_join_branch_applies_the_same_type_clause(lib):
    lib.put(lib.checkpoints, "flux1-dev-fp8.safetensors", all_in_one())
    lib.index()
    model = lib.model("flux1-dev-fp8.safetensors")
    tag_id = _create_tag("flux")
    _attach_tag(model.id, tag_id)

    found = model_repo.get_all(model_type="diffusion_model", tag_ids=[tag_id], include_extractable_checkpoints=True)
    plain = model_repo.get_all(model_type="diffusion_model", tag_ids=[tag_id])

    assert [m.filename for m in found] == ["flux1-dev-fp8.safetensors"]
    assert plain == []


def _create_tag(name):
    from src.platform.database.database import db
    from src.platform.util.ids import generate_ulid

    tag_id = generate_ulid()
    with db.get_cursor() as cursor:
        cursor.execute("INSERT INTO tags (id, name) VALUES (?, ?)", (tag_id, name))
    return tag_id


def _attach_tag(model_id, tag_id):
    from src.platform.database.database import db

    with db.get_cursor() as cursor:
        cursor.execute("INSERT INTO model_tags (model_id, tag_id) VALUES (?, ?)", (model_id, tag_id))


def test_the_generation_resolves_an_offered_checkpoint_in_a_diffusion_model_field(lib):
    lib.put(lib.checkpoints, "flux1-dev-fp8.safetensors", all_in_one())
    lib.index()
    offered = listing()[0]
    locator = ModelLocator(lib.scanner.resolver, lib.scanner.locations)
    backend = Mock(backend_id="native-local")

    resolved = resolve_form_model_refs({"diffusion_model": make_model_ref(offered["id"])}, backend, locator)

    assert resolved == {"diffusion_model": str(lib.checkpoints / "flux1-dev-fp8.safetensors")}


def test_a_stale_extractable_verdict_is_read_again_once_the_registry_changes():
    row = {"status": "decided", "transformer_extractable": 1, "registry_fingerprint": "old"}
    assert verdict_is_current(row, "old") is True
    assert verdict_is_current(row, "new") is False
    assert verdict_is_current({**row, "transformer_extractable": 0}, "new") is True
    assert verdict_is_current({**row, "status": "undecided", "transformer_extractable": 0}, "new") is False
