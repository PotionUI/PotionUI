import pytest

from src.platform.database.rows import now_iso
from src.platform.filesystem.model_roots import (
    BindingNotFoundError,
    LogicalLocation,
    ModelRootResolver,
    RootProbe,
    binding_subdir_key,
    root_path_key,
)
from src.platform.filesystem.model_roots_repository import ModelRootRepository


def _root(repo, root_id, path, *, case_insensitive=False):
    repo.insert_root(
        root_id, root_id, path, root_path_key(path, case_insensitive=case_insensitive), "library", False,
        case_insensitive, now_iso(),
    )


@pytest.mark.parametrize(
    ("subdir", "case_insensitive", "expected"),
    [
        ("loras", False, "loras"),
        ("Loras/", False, "Loras"),
        ("Data\\Models\\Lora", False, "Data/Models/Lora"),
        ("/Data/Models/", False, "Data/Models"),
        ("Loras", True, "loras"),
        ("", False, ""),
        (".", False, ""),
        ("café", False, "café"),
        ("café", False, "café"),
    ],
)
def test_the_subdir_key_is_a_normalised_posix_path(subdir, case_insensitive, expected):
    assert binding_subdir_key(subdir, case_insensitive=case_insensitive) == expected


def test_a_root_holds_several_folders_of_one_type(mock_db):
    repo = ModelRootRepository()
    _root(repo, "r1", "/x/r1")

    first = repo.insert_binding("r1", "lora", "Lora", 10, False)
    second = repo.insert_binding("r1", "lora", "LyCORIS", 11, False)

    assert first != second
    assert [b["id"] for b in repo.bindings_for("r1", "lora")] == [first, second]
    assert repo.get_binding(second)["subdir"] == "LyCORIS"


def test_the_same_folder_cannot_be_bound_twice_for_a_type(mock_db):
    repo = ModelRootRepository()
    _root(repo, "r1", "/x/r1")
    repo.insert_binding("r1", "lora", "Lora", 10, False)

    with pytest.raises(Exception):
        repo.insert_binding("r1", "lora", "Lora/", 11, False)


def test_a_case_insensitive_root_treats_spellings_as_one_folder(mock_db):
    repo = ModelRootRepository()
    _root(repo, "r1", "/x/r1", case_insensitive=True)
    first = repo.insert_binding("r1", "lora", "Lora", 10, False)

    assert repo.find_binding("r1", "lora", "LORA")["id"] == first
    assert repo.upsert_binding("r1", "lora", "lora", 11) == first
    assert len(repo.bindings_for("r1", "lora")) == 1


def test_a_case_sensitive_root_keeps_spellings_apart(mock_db):
    repo = ModelRootRepository()
    _root(repo, "r1", "/x/r1")
    repo.insert_binding("r1", "lora", "Lora", 10, False)

    assert repo.find_binding("r1", "lora", "lora") is None
    repo.upsert_binding("r1", "lora", "lora", 11)
    assert len(repo.bindings_for("r1", "lora")) == 2


def test_upsert_returns_the_existing_binding_and_only_changes_an_explicit_flag(mock_db):
    repo = ModelRootRepository()
    _root(repo, "r1", "/x/r1")
    first = repo.insert_binding("r1", "checkpoint", "checkpoints", 10, False, scan_headers=False)

    assert repo.upsert_binding("r1", "checkpoint", "checkpoints", 99) == first
    assert repo.get_binding(first)["scan_headers"] == 0
    assert repo.get_binding(first)["position"] == 10
    repo.upsert_binding("r1", "checkpoint", "checkpoints", 99, scan_headers=True)
    assert repo.get_binding(first)["scan_headers"] == 1


def test_scan_headers_can_target_one_folder(mock_db):
    repo = ModelRootRepository()
    _root(repo, "r1", "/x/r1")
    repo.insert_binding("r1", "checkpoint", "a", 10, False, scan_headers=False)
    repo.insert_binding("r1", "checkpoint", "b", 11, False, scan_headers=False)

    assert repo.set_scan_headers("r1", "checkpoint", True, "b") is True
    assert repo.set_scan_headers("r1", "checkpoint", True, "missing") is False

    assert {b["subdir"]: b["scan_headers"] for b in repo.bindings_for("r1", "checkpoint")} == {"a": 0, "b": 1}


def test_write_moves_between_folders_of_one_type(mock_db):
    repo = ModelRootRepository()
    _root(repo, "r1", "/x/r1")
    first = repo.insert_binding("r1", "lora", "Lora", 10, False)
    second = repo.insert_binding("r1", "lora", "LyCORIS", 11, False)

    assert repo.set_write("r1", "lora", "LyCORIS") == second
    assert [b["id"] for b in repo.list_bindings("lora") if b["is_write"] and b["root_id"] == "r1"] == [second]
    assert repo.set_write("r1", "lora") == first
    assert [b["is_write"] for b in repo.bindings_for("r1", "lora")] == [1, 0]


def test_write_on_a_missing_folder_is_refused(mock_db):
    repo = ModelRootRepository()
    _root(repo, "r1", "/x/r1")
    repo.insert_binding("r1", "lora", "Lora", 10, False)

    with pytest.raises(BindingNotFoundError):
        repo.set_write("r1", "lora", "Nope")


def test_deleting_one_folder_leaves_its_sibling(mock_db):
    repo = ModelRootRepository()
    _root(repo, "r1", "/x/r1")
    repo.insert_binding("r1", "lora", "Lora", 10, False)
    keep = repo.insert_binding("r1", "lora", "LyCORIS", 11, False)

    assert repo.delete_binding("r1", "lora", "Lora") == 1

    assert [b["id"] for b in repo.bindings_for("r1", "lora")] == [keep]
    assert repo.delete_binding("r1", "lora") == 1
    assert repo.bindings_for("r1", "lora") == []


def test_reordering_by_binding_id_swaps_positions(mock_db):
    repo = ModelRootRepository()
    _root(repo, "r1", "/x/r1")
    first = repo.insert_binding("r1", "lora", "Lora", 500, False)
    second = repo.insert_binding("r1", "lora", "LyCORIS", 501, False)

    home = [b["id"] for b in repo.list_bindings("lora") if b["root_id"] == "home"]

    repo.reorder_bindings("lora", [second, *home, first])

    assert [b["id"] for b in repo.list_bindings("lora")] == [second, *home, first]


def test_the_resolver_carries_binding_ids_and_physical_follows_them(mock_db, tmp_path):
    repo = ModelRootRepository()
    _root(repo, "r1", str(tmp_path / "r1"))
    first = repo.insert_binding("r1", "lora", "Lora", 500, False)
    second = repo.insert_binding("r1", "lora", "LyCORIS", 501, False)
    resolver = ModelRootResolver(repo, RootProbe(), tmp_path)

    assert [td.binding_id for td in resolver.type_dirs("lora", online_only=False) if td.root_id == "r1"] == [
        first, second,
    ]
    assert resolver.binding_dir(second).subdir == "LyCORIS"
    assert resolver.physical(LogicalLocation("r1", "lora", "x.safetensors", second)) == (
        tmp_path / "r1" / "LyCORIS" / "x.safetensors"
    )
    assert resolver.physical(LogicalLocation("r1", "lora", "x.safetensors")) == (
        tmp_path / "r1" / "Lora" / "x.safetensors"
    )
    assert resolver.binding_dir("nope") is None
