from src.features.models.locations_repository import ModelLocationsRepository
from src.features.models.records import Model
from src.features.models.repository import model_repo
from src.platform.database.rows import now_iso
from src.platform.filesystem.model_roots_repository import ModelRootRepository


def _root(root_id: str, label: str) -> None:
    ModelRootRepository().insert_root(root_id, label, f"/models/{root_id}", root_id, "library", False, False, now_iso())


_POSITION_OFFSET = 2000


def _binding(root_id: str, model_type: str, position: int) -> None:
    ModelRootRepository().insert_binding(root_id, model_type, model_type, position + _POSITION_OFFSET, False)


def _model(filename: str, model_type: str) -> Model:
    return model_repo.create(Model(filename=filename, file_size=1, sha256=filename, model_type=model_type))


def _location(model_id: str, root_id: str, model_type: str, rel_path: str) -> None:
    ModelLocationsRepository().upsert(
        model_id=model_id, root_id=root_id, model_type=model_type,
        rel_path=rel_path, rel_key=rel_path, size=1, mtime_ns=None,
        sha256=rel_path, status="present", seen_at=now_iso(),
    )


def test_list_duplicate_models_marks_the_first_folder_as_winner(mock_db):
    _root("root-a", "Root A")
    _root("root-b", "Root B")
    _binding("root-a", "checkpoint", 0)
    _binding("root-b", "checkpoint", 1)

    model = _model("shared.safetensors", "checkpoint")
    _location(model.id, "root-b", "checkpoint", "shared.safetensors")
    _location(model.id, "root-a", "checkpoint", "shared.safetensors")

    entries = ModelLocationsRepository().list_duplicate_models()

    assert len(entries) == 1
    entry = entries[0]
    assert entry["model_type"] == "checkpoint"
    assert entry["filename"] == "shared.safetensors"
    assert "model_id" not in entry

    copies_by_root = {copy["root_label"]: copy for copy in entry["copies"]}
    assert copies_by_root["Root A"]["winner"] is True
    assert copies_by_root["Root B"]["winner"] is False


def test_list_duplicate_models_ignores_models_with_a_single_copy(mock_db):
    _root("root-a", "Root A")
    _binding("root-a", "checkpoint", 0)

    model = _model("solo.safetensors", "checkpoint")
    _location(model.id, "root-a", "checkpoint", "solo.safetensors")

    assert ModelLocationsRepository().list_duplicate_models() == []


def test_list_conflicts_carries_the_root_label(mock_db):
    _root("root-a", "Root A")
    _binding("root-a", "checkpoint", 0)

    model = _model("conflict.safetensors", "checkpoint")
    ModelLocationsRepository().upsert(
        model_id=model.id, root_id="root-a", model_type="checkpoint",
        rel_path="conflict.safetensors", rel_key="conflict.safetensors", size=1, mtime_ns=None,
        sha256="conflict", status="conflict", seen_at=now_iso(),
    )

    entries = ModelLocationsRepository().list_conflicts()

    assert len(entries) == 1
    assert entries[0]["root_label"] == "Root A"
    assert entries[0]["rel_path"] == "conflict.safetensors"


def _location_with(model_id: str, root_id: str, model_type: str, rel_path: str, status: str = "present") -> None:
    ModelLocationsRepository().upsert(
        model_id=model_id, root_id=root_id, model_type=model_type,
        rel_path=rel_path, rel_key=rel_path, size=1, mtime_ns=None,
        sha256=rel_path, status=status, seen_at=now_iso(),
    )


def _mismatch_library() -> None:
    _root("root-a", "Root A")
    _binding("root-a", "checkpoint", 0)


def test_type_mismatches_returns_a_present_file_whose_model_was_retyped(mock_db):
    _mismatch_library()
    model = _model("flux.safetensors", "diffusion_model")
    _location_with(model.id, "root-a", "checkpoint", "flux.safetensors")

    rows = ModelLocationsRepository().type_mismatches("checkpoint", ["flux.safetensors", "other.safetensors"])

    assert [(r["rel_path"], r["model_id"], r["model_type"], r["filename"]) for r in rows] == [
        ("flux.safetensors", model.id, "diffusion_model", "flux.safetensors")
    ]


def test_type_mismatches_skips_a_missing_location(mock_db):
    _mismatch_library()
    model = _model("gone.safetensors", "diffusion_model")
    _location_with(model.id, "root-a", "checkpoint", "gone.safetensors", status="missing")

    assert ModelLocationsRepository().type_mismatches("checkpoint", ["gone.safetensors"]) == []


def test_type_mismatches_skips_a_model_that_kept_the_binding_type(mock_db):
    _mismatch_library()
    model = _model("same.safetensors", "checkpoint")
    _location_with(model.id, "root-a", "checkpoint", "same.safetensors")

    assert ModelLocationsRepository().type_mismatches("checkpoint", ["same.safetensors"]) == []


def test_type_mismatches_only_looks_at_the_asked_binding_type(mock_db):
    _root("root-a", "Root A")
    _binding("root-a", "checkpoint", 0)
    _binding("root-a", "lora", 1)
    model = _model("thing.safetensors", "diffusion_model")
    _location_with(model.id, "root-a", "lora", "thing.safetensors")

    assert ModelLocationsRepository().type_mismatches("checkpoint", ["thing.safetensors"]) == []


def test_type_mismatches_returns_every_match_across_the_chunk_boundary(mock_db):
    _mismatch_library()
    names = [f"m{index:04d}.safetensors" for index in range(1203)]
    for name in names:
        model = _model(name, "diffusion_model")
        _location_with(model.id, "root-a", "checkpoint", name)

    rows = ModelLocationsRepository().type_mismatches("checkpoint", names + names[:10] + [""])

    assert sorted(r["rel_path"] for r in rows) == sorted(names)


def test_type_mismatches_with_nothing_to_look_up_returns_nothing(mock_db):
    assert ModelLocationsRepository().type_mismatches("checkpoint", []) == []
    assert ModelLocationsRepository().type_mismatches("checkpoint", [""]) == []
