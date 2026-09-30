"""Rewriting `model:<id>` form values into engine-native refs."""

import pytest
from unittest.mock import patch, Mock

from src.features.models import form_refs as fr


@pytest.fixture(autouse=True)
def typed_models():
    with patch("src.features.models.repository.ModelRepository.undefined_filenames", return_value={}) as spy:
        yield spy


def _avail(ref=None, confidence="verified", digest=None, backend_id="comfy"):
    """A stand-in for `ModelAvailability` carrying only what `resolve_form_model_refs`
    reads: `ref`, `confidence`, `digest`, `backend_id`."""
    return Mock(ref=ref, confidence=confidence, digest=digest, backend_id=backend_id)


def _backend(backend_id, resolve_ref=None):
    backend = Mock()
    backend.backend_id = backend_id
    backend.resolve_ref = Mock(side_effect=resolve_ref) if resolve_ref else Mock(side_effect=lambda ref: ref)
    return backend


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


def test_substitute_strings_rewrites_mapped_values_through_nested_shapes():
    form = {
        "checkpoint": fr.make_model_ref("m1"),
        "loras": [{"model": fr.make_model_ref("m2"), "strength": 0.8}],
    }
    mapping = {fr.make_model_ref("m1"): "checkpoint.safetensors", fr.make_model_ref("m2"): "lora.safetensors"}

    result = fr.substitute_strings(form, mapping)

    assert result == {
        "checkpoint": "checkpoint.safetensors",
        "loras": [{"model": "lora.safetensors", "strength": 0.8}],
    }


def test_substitute_strings_leaves_unmapped_values_untouched():
    form = {"checkpoint": fr.make_model_ref("m1"), "strength": 0.8, "count": 3}

    result = fr.substitute_strings(form, {})

    assert result == form


@patch.object(fr, "model_availability_repo")
def test_unindexed_backend_uses_filename_when_the_locator_has_no_location(repo):
    locator = Mock()
    repo.any_indexed.return_value = False
    repo.get.return_value = None
    locator.path_for_model.side_effect = fr.ModelFileUnavailable("no location")

    with patch("src.features.models.repository.model_repo") as mr:
        mr.undefined_filenames.return_value = {}
        mr.get_by_id.return_value = type("M", (), {"filename": "detail.safetensors"})()
        resolved = fr.resolve_form_model_refs({"lora": fr.make_model_ref("m1")}, _backend("comfy"), locator)

    assert resolved["lora"] == "detail.safetensors"


@patch.object(fr, "model_availability_repo")
def test_unindexed_backend_prefers_the_locators_live_location(repo):
    locator = Mock()
    repo.any_indexed.return_value = False
    repo.get.return_value = None
    locator.path_for_model.return_value = "/lib/loras/detail.safetensors"

    with patch("src.features.models.repository.model_repo") as mr:
        mr.undefined_filenames.return_value = {}
        mr.get_by_id.return_value = type("M", (), {"filename": "detail.safetensors"})()
        resolved = fr.resolve_form_model_refs({"lora": fr.make_model_ref("m1")}, _backend("comfy"), locator)

    assert resolved["lora"] == "/lib/loras/detail.safetensors"


@patch.object(fr, "model_availability_repo")
def test_indexed_backend_missing_a_model_still_raises(repo):
    """Once indexed, absence is a fact, not ignorance."""
    repo.any_indexed.return_value = True
    repo.get.return_value = None

    with patch("src.features.models.repository.model_repo") as mr:
        mr.undefined_filenames.return_value = {}
        mr.get_by_id.return_value = type("M", (), {"filename": "detail.safetensors"})()
        with pytest.raises(fr.ModelRefNotAvailableError):
            fr.resolve_form_model_refs({"lora": fr.make_model_ref("m1")}, _backend("comfy"), Mock())


@patch.object(fr, "model_availability_repo")
def test_resolves_each_reference_to_the_backends_own_ref(repo):
    repo.any_indexed.return_value = True
    repo.get.side_effect = lambda mid, bid: {
        ("m1", "comfy"): _avail("style/detail.safetensors"),
        ("m2", "comfy"): _avail("upscale.pth"),
    }[(mid, bid)]

    form = {"checkpoint": fr.make_model_ref("m1"), "loras": [{"model": fr.make_model_ref("m2"), "strength": 1.0}]}
    resolved = fr.resolve_form_model_refs(form, _backend("comfy"), Mock())

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

    assert fr.resolve_form_model_refs(form, _backend("local"), Mock())["lora"] == "models/loras/detail.safetensors"
    assert fr.resolve_form_model_refs(form, _backend("comfy"), Mock())["lora"] == "style/detail.safetensors"


@patch.object(fr, "model_availability_repo")
def test_indexed_rows_are_passed_through_the_backends_own_resolve_ref(repo):
    repo.any_indexed.return_value = True
    repo.get.return_value = _avail("loras/x.safetensors", backend_id="native-1")
    backend = _backend("native-1", resolve_ref=lambda ref: f"/abs/models/{ref}")

    resolved = fr.resolve_form_model_refs({"lora": fr.make_model_ref("m1")}, backend, Mock())

    assert resolved["lora"] == "/abs/models/loras/x.safetensors"
    backend.resolve_ref.assert_called_once_with("loras/x.safetensors")


@patch.object(fr, "model_availability_repo")
def test_offline_root_from_resolve_ref_raises_naming_the_root(repo):
    from src.features.models.locator import ModelFileUnavailable

    repo.any_indexed.return_value = True
    repo.get.return_value = _avail("loras/x.safetensors", backend_id="native-1")

    def _raise_offline(ref):
        raise ModelFileUnavailable(f"is on 'NAS models' (offline)", root_label="NAS models")

    backend = _backend("native-1", resolve_ref=_raise_offline)

    with patch("src.features.models.repository.model_repo") as mr:
        mr.undefined_filenames.return_value = {}
        mr.get_by_id.return_value = type("M", (), {"filename": "x.safetensors"})()
        with pytest.raises(fr.ModelRefNotAvailableError) as exc:
            fr.resolve_form_model_refs({"lora": fr.make_model_ref("m1")}, backend, Mock())

    assert "x.safetensors" in str(exc.value)
    assert "NAS models" in str(exc.value)
    assert "offline" in str(exc.value)


@patch.object(fr, "model_availability_repo")
def test_fallback_ref_never_goes_through_resolve_ref(repo):
    locator = Mock()
    repo.any_indexed.return_value = False
    repo.get.return_value = None
    locator.path_for_model.side_effect = fr.ModelFileUnavailable("no location")
    backend = _backend("native-1")

    with patch("src.features.models.repository.model_repo") as mr:
        mr.undefined_filenames.return_value = {}
        mr.get_by_id.return_value = type("M", (), {"filename": "detail.safetensors"})()
        fr.resolve_form_model_refs({"lora": fr.make_model_ref("m1")}, backend, locator)

    backend.resolve_ref.assert_not_called()


@patch.object(fr, "model_availability_repo")
def test_form_without_references_is_returned_untouched_without_querying(repo):
    form = {"checkpoint": "models/checkpoints/a.safetensors", "steps": 30}
    assert fr.resolve_form_model_refs(form, _backend("local"), Mock()) is form
    repo.get.assert_not_called()


@patch.object(fr, "model_availability_repo")
def test_unresolvable_reference_raises_naming_the_model(repo):
    """Passing `model:<ulid>` into a pipeline would fail opaquely inside the engine."""
    repo.any_indexed.return_value = True
    repo.get.return_value = None

    with patch("src.features.models.repository.model_repo") as mr:
        mr.undefined_filenames.return_value = {}
        mr.get_by_id.return_value = type("M", (), {"filename": "detail.safetensors"})()
        with pytest.raises(fr.ModelRefNotAvailableError) as exc:
            fr.resolve_form_model_refs({"lora": fr.make_model_ref("m1")}, _backend("comfy"), Mock())

    assert "detail.safetensors" in str(exc.value)
    assert "comfy" in str(exc.value)


@patch.object(fr, "model_availability_repo")
def test_non_string_values_survive_rewriting(repo):
    repo.any_indexed.return_value = True
    repo.get.return_value = _avail("detail.safetensors")
    form = {"m": fr.make_model_ref("m1"), "steps": 30, "cfg": 5.5, "hires": True, "seed": None}

    resolved = fr.resolve_form_model_refs(form, _backend("local"), Mock())

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
        mr.undefined_filenames.return_value = {}
        mr.get_by_id.return_value = type(
            "M", (), {"filename": "flux.safetensors", "sha256": "cafebabe" * 8}
        )()
        with pytest.raises(fr.ModelDigestConflictError) as exc:
            fr.resolve_form_model_refs({"checkpoint": fr.make_model_ref("m1")}, _backend("remote1"), Mock())

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
        mr.undefined_filenames.return_value = {}
        mr.get_by_id.return_value = type("M", (), {"filename": "bad.safetensors", "sha256": None})()
        with pytest.raises(fr.ModelDigestConflictError):
            fr.resolve_form_model_refs(
                {"checkpoint": fr.make_model_ref("good"), "lora": fr.make_model_ref("conflicted")},
                _backend("comfy"),
                Mock(),
            )


def test_collect_model_refs_records_every_occurrence_with_its_path():
    """Unlike `collect_model_ids`, a reference picked twice is two entries, each
    with the exact dict-key / list-index route the bundle importer rewrites."""
    form = {
        "checkpoint": fr.make_model_ref("m1"),
        "refiner": fr.make_model_ref("m1"),
        "loras": [{"model": fr.make_model_ref("m2"), "strength": 0.8}],
        "caption": "m1",
    }
    assert fr.collect_model_refs(form) == [
        {"path": ["checkpoint"], "model_id": "m1"},
        {"path": ["refiner"], "model_id": "m1"},
        {"path": ["loras", 0, "model"], "model_id": "m2"},
    ]


def test_get_at_path_returns_the_sentinel_for_any_stale_path():
    form = {"loras": [{"model": "a"}], "checkpoint": "c"}
    assert fr.get_at_path(form, ["loras", 0, "model"]) == "a"
    assert fr.get_at_path(form, ["loras", 1, "model"]) is fr._MISSING
    assert fr.get_at_path(form, ["missing"]) is fr._MISSING
    assert fr.get_at_path(form, ["checkpoint", "deeper"]) is fr._MISSING
    assert fr.get_at_path(form, ["loras", "model"]) is fr._MISSING


def test_set_at_path_copies_only_the_containers_on_the_path():
    form = {"loras": [{"model": "a", "strength": 0.5}, {"model": "b"}], "checkpoint": "c"}
    out = fr.set_at_path(form, ["loras", 0, "model"], "X")
    assert out["loras"][0] == {"model": "X", "strength": 0.5}
    assert out["loras"][1] is form["loras"][1]
    assert form["loras"][0]["model"] == "a"
    assert out["checkpoint"] == "c"


def test_set_at_path_is_a_no_op_for_a_stale_path_and_keeps_tuples_as_tuples():
    form = {"loras": ({"model": "a"},), "checkpoint": "c"}
    assert fr.set_at_path(form, ["loras", 3, "model"], "X") == form
    assert fr.set_at_path(form, ["missing"], "X") is form
    out = fr.set_at_path(form, ["loras", 0, "model"], "X")
    assert isinstance(out["loras"], tuple)
    assert out["loras"][0]["model"] == "X"


@patch.object(fr, "model_availability_repo")
def test_a_model_without_a_type_is_refused_by_name(repo, typed_models):
    typed_models.return_value = {"m1": "mystery.safetensors"}

    with pytest.raises(fr.ModelRefNotAvailableError) as raised:
        fr.resolve_form_model_refs({"lora": fr.make_model_ref("m1")}, _backend("comfy"), Mock())

    assert "'mystery.safetensors' needs a type" in str(raised.value)
    repo.get.assert_not_called()
