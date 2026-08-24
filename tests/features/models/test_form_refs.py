"""Rewriting `model:<id>` form values into engine-native refs."""

import pytest
from unittest.mock import patch, Mock

from src.features.models import form_refs as fr


def _avail(ref=None, confidence="verified", digest=None, backend_id="comfy"):
    """A stand-in for `ModelAvailability` carrying only what `resolve_form_model_refs`
    reads: `ref`, `confidence`, `digest`, `backend_id`."""
    return Mock(ref=ref, confidence=confidence, digest=digest, backend_id=backend_id)


def test_collects_nothing_from_legacy_path_values():
    """Saved sessions and preset defaults hold plain strings; they are not references."""
    form = {"checkpoint": "models/checkpoints/a.safetensors", "vae": "vae_model.safetensors"}
    assert fr.collect_model_ids(form) == []


def test_collects_from_nested_lora_picker_shape():
    """The LoRA picker stores [{model, strength}, ...]; recursion handles it."""
    form = {
        "checkpoint": fr.make_model_ref("m1"),
        "loras": [
            {"model": fr.make_model_ref("m2"), "strength": 0.8},
            {"model": fr.make_model_ref("m3"), "strength": 0.5},
        ],
    }
    assert fr.collect_model_ids(form) == ["m1", "m2", "m3"]


def test_duplicate_references_collected_once_in_first_seen_order():
    form = {"a": fr.make_model_ref("m2"), "b": fr.make_model_ref("m1"), "c": fr.make_model_ref("m2")}
    assert fr.collect_model_ids(form) == ["m2", "m1"]


@patch.object(fr, "model_availability_repo")
def test_unindexed_backend_falls_back_to_the_model_index(repo):
    """A configured-but-unindexed backend has no rows, but it does hold models.
    Failing here would break every generation on that engine before its first index."""
    repo.any_indexed.return_value = False
    repo.get.return_value = None

    with patch("src.features.models.repository.model_repo") as mr:
        mr.get_by_id.return_value = type(
            "M", (), {"file_path": "models/loras/detail.safetensors", "filename": "detail.safetensors"}
        )()
        resolved = fr.resolve_form_model_refs({"lora": fr.make_model_ref("m1")}, "comfy")

    assert resolved["lora"] == "models/loras/detail.safetensors"


@patch.object(fr, "model_availability_repo")
def test_unindexed_backend_uses_filename_when_there_is_no_local_path(repo):
    """A remote-only model has file_path=None; a ComfyUI server resolves bare names."""
    repo.any_indexed.return_value = False
    repo.get.return_value = None

    with patch("src.features.models.repository.model_repo") as mr:
        mr.get_by_id.return_value = type(
            "M", (), {"file_path": None, "filename": "detail.safetensors"}
        )()
        resolved = fr.resolve_form_model_refs({"lora": fr.make_model_ref("m1")}, "comfy")

    assert resolved["lora"] == "detail.safetensors"


@patch.object(fr, "model_availability_repo")
def test_indexed_backend_missing_a_model_still_raises(repo):
    """Once indexed, absence is a fact, not ignorance."""
    repo.any_indexed.return_value = True
    repo.get.return_value = None

    with patch("src.features.models.repository.model_repo") as mr:
        mr.get_by_id.return_value = type("M", (), {"filename": "detail.safetensors"})()
        with pytest.raises(fr.ModelRefNotAvailableError):
            fr.resolve_form_model_refs({"lora": fr.make_model_ref("m1")}, "comfy")


@patch.object(fr, "model_availability_repo")
def test_resolves_each_reference_to_the_backends_own_ref(repo):
    repo.any_indexed.return_value = True
    repo.get.side_effect = lambda mid, bid: {
        ("m1", "comfy"): _avail("style/detail.safetensors"),
        ("m2", "comfy"): _avail("upscale.pth"),
    }[(mid, bid)]

    form = {"checkpoint": fr.make_model_ref("m1"), "loras": [{"model": fr.make_model_ref("m2"), "strength": 1.0}]}
    resolved = fr.resolve_form_model_refs(form, "comfy")

    assert resolved["checkpoint"] == "style/detail.safetensors"
    assert resolved["loras"][0] == {"model": "upscale.pth", "strength": 1.0}


@patch.object(fr, "model_availability_repo")
def test_native_and_comfy_get_different_refs_for_the_same_model(repo):
    repo.any_indexed.return_value = True
    refs = {
        ("m1", "local"): _avail("models/loras/detail.safetensors"),
        ("m1", "comfy"): _avail("style/detail.safetensors"),
    }
    repo.get.side_effect = lambda mid, bid: refs[(mid, bid)]
    form = {"lora": fr.make_model_ref("m1")}

    assert fr.resolve_form_model_refs(form, "local")["lora"] == "models/loras/detail.safetensors"
    assert fr.resolve_form_model_refs(form, "comfy")["lora"] == "style/detail.safetensors"


@patch.object(fr, "model_availability_repo")
def test_form_without_references_is_returned_untouched_without_querying(repo):
    form = {"checkpoint": "models/checkpoints/a.safetensors", "steps": 30}
    assert fr.resolve_form_model_refs(form, "local") is form
    repo.get.assert_not_called()


@patch.object(fr, "model_availability_repo")
def test_unresolvable_reference_raises_naming_the_model(repo):
    """Passing `model:<ulid>` into a pipeline would fail opaquely inside the engine."""
    repo.any_indexed.return_value = True
    repo.get.return_value = None

    with patch("src.features.models.repository.model_repo") as mr:
        mr.get_by_id.return_value = type("M", (), {"filename": "detail.safetensors"})()
        with pytest.raises(fr.ModelRefNotAvailableError) as exc:
            fr.resolve_form_model_refs({"lora": fr.make_model_ref("m1")}, "comfy")

    assert "detail.safetensors" in str(exc.value)
    assert "comfy" in str(exc.value)


@patch.object(fr, "model_availability_repo")
def test_non_string_values_survive_rewriting(repo):
    repo.any_indexed.return_value = True
    repo.get.return_value = _avail("detail.safetensors")
    form = {"m": fr.make_model_ref("m1"), "steps": 30, "cfg": 5.5, "hires": True, "seed": None}

    resolved = fr.resolve_form_model_refs(form, "local")

    assert resolved["steps"] == 30 and resolved["cfg"] == 5.5
    assert resolved["hires"] is True and resolved["seed"] is None


# --- digest conflict blocks routing ----------------------------------------------

@patch.object(fr, "model_availability_repo")
def test_digest_conflict_raises_instead_of_resolving(repo):
    """A conflicted row must never reach a pipeline as a usable ref."""
    repo.any_indexed.return_value = True
    repo.get.return_value = _avail(
        ref="models/checkpoints/flux.safetensors",
        confidence="conflict",
        digest="deadbeef" * 8,
        backend_id="remote1",
    )

    with patch("src.features.models.repository.model_repo") as mr:
        mr.get_by_id.return_value = type(
            "M", (), {"filename": "flux.safetensors", "sha256": "cafebabe" * 8}
        )()
        with pytest.raises(fr.ModelDigestConflictError) as exc:
            fr.resolve_form_model_refs({"checkpoint": fr.make_model_ref("m1")}, "remote1")

    message = str(exc.value)
    assert "flux.safetensors" in message
    assert "remote1" in message
    assert "cafebabe" in message  # expected digest, from the canonical model row
    assert "deadbeef" in message  # found digest, from the conflicted row


@patch.object(fr, "model_availability_repo")
def test_digest_conflict_is_reported_even_when_other_models_resolve_cleanly(repo):
    """One bad model must not be silently dropped just because others resolved."""
    repo.any_indexed.return_value = True

    def _get(mid, bid):
        if mid == "good":
            return _avail(ref="ok.safetensors", confidence="verified")
        return _avail(ref="bad.safetensors", confidence="conflict", digest="bad" * 20, backend_id=bid)

    repo.get.side_effect = _get

    with patch("src.features.models.repository.model_repo") as mr:
        mr.get_by_id.return_value = type("M", (), {"filename": "bad.safetensors", "sha256": None})()
        with pytest.raises(fr.ModelDigestConflictError):
            fr.resolve_form_model_refs(
                {"checkpoint": fr.make_model_ref("good"), "lora": fr.make_model_ref("conflicted")},
                "comfy",
            )
