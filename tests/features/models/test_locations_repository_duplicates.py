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
