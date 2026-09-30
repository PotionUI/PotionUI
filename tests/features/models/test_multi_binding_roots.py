from pathlib import Path

import pytest

from src.features.models.indexer import ModelScanner
from src.features.models.locations_repository import ModelLocationsRepository
from src.features.models.locator import ModelFileUnavailable, ModelLocator
from src.features.models.records import Model
from src.features.models.repository import model_repo
from src.features.models.roots import (
    BindingNestedError,
    BindingRef,
    BindingSpec,
    DuplicateBindingError,
    InvalidBindingError,
    ModelRootsManager,
    RootReadOnlyRefusalError,
)
from src.platform.database.rows import now_iso
from src.platform.filesystem.model_roots import ModelRootResolver, RootProbe
from src.platform.filesystem.model_roots_repository import ModelRootRepository
from src.platform.settings.repository import SettingRepository


class _Coordinator:
    def cancel_and_restart(self, trigger="location_change"):
        return {"state": "idle"}

    def status(self):
        return {"state": "idle"}


@pytest.fixture
def stack(mock_db, tmp_path):
    repository = ModelRootRepository()
    probe = RootProbe(ttl_seconds=0.0)
    resolver = ModelRootResolver(repository, probe, tmp_path)
    manager = ModelRootsManager(
        repository=repository,
        resolver=resolver,
        probe=probe,
        indexing_coordinator=_Coordinator(),
        setting_repository=SettingRepository(),
        base_dir=tmp_path,
    )
    scanner = ModelScanner(resolver)
    locator = ModelLocator(resolver)
    return type("Stack", (), {
        "repository": repository, "resolver": resolver, "manager": manager,
        "scanner": scanner, "locator": locator, "base": tmp_path,
    })


def _put(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _matrix(stack) -> Path:
    library = stack.base / "sm" / "Models"
    for folder in ("Lora", "LyCORIS", "Embeddings"):
        (library / folder).mkdir(parents=True)
    return library


def _create(stack, library: Path, **kwargs):
    return stack.manager.create_root(
        str(library),
        label="StabilityMatrix",
        bindings=[
            BindingSpec("lora", "Lora"),
            BindingSpec("lora", "LyCORIS"),
            BindingSpec("embedding", "Embeddings"),
        ],
        write_types=kwargs.pop("write_types", ["lora"]),
        **kwargs,
    )


def _model_ids(filename: str):
    return [m.id for m in model_repo.get_by_filename(filename)]


def _binding_id(stack, root_id: str, subdir: str) -> str:
    return stack.repository.find_binding(root_id, "lora", subdir)["id"]


class TestSeveralFoldersOfOneType:

    def test_a_root_binds_two_folders_of_one_type(self, stack):
        root = _create(stack, _matrix(stack))

        lora = [b for b in root["bindings"] if b["model_type"] == "lora"]

        assert [b["subdir"] for b in lora] == ["Lora", "LyCORIS"]
        assert len({b["binding_id"] for b in lora}) == 2
        assert [b["position"] for b in lora] == sorted(b["position"] for b in lora)
        assert [b["is_write"] for b in lora] == [True, False]

    def test_indexing_finds_files_in_both_folders(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)
        _put(library / "Lora" / "a.safetensors", b"A")
        _put(library / "Lora" / "nested" / "b.safetensors", b"B")
        _put(library / "LyCORIS" / "c.safetensors", b"C")

        result = stack.scanner.index_models(max_workers=1)

        assert result["indexed"] == 3
        by_binding = {
            b["binding_id"]: b["indexed_files"]
            for b in stack.manager.get_overview()["roots"][-1]["bindings"]
            if b["model_type"] == "lora"
        }
        assert sorted(by_binding.values()) == [1, 2]
        assert root["id"] in {loc["root_id"] for m in model_repo.get_all() for loc in stack.scanner.locations.list_for_model(m.id)}

    def test_each_model_resolves_to_its_own_file(self, stack):
        library = _matrix(stack)
        _create(stack, library)
        first = _put(library / "Lora" / "a.safetensors", b"A")
        second = _put(library / "LyCORIS" / "c.safetensors", b"C")
        stack.scanner.index_models(max_workers=1)

        assert stack.locator.path_for_model(_model_ids("a.safetensors")[0]) == first
        assert stack.locator.path_for_model(_model_ids("c.safetensors")[0]) == second
        assert stack.locator.path_for_ref("loras/c.safetensors") == second

    def test_a_path_maps_back_to_the_folder_it_is_in(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)
        first = _put(library / "Lora" / "a.safetensors", b"A")
        second = _put(library / "LyCORIS" / "c.safetensors", b"C")

        a = stack.resolver.to_logical(first)
        c = stack.resolver.to_logical(second)

        assert a.binding_id == _binding_id(stack, root["id"], "Lora")
        assert c.binding_id == _binding_id(stack, root["id"], "LyCORIS")
        assert a.logical_ref == "loras/a.safetensors"
        assert c.logical_ref == "loras/c.safetensors"
        assert stack.resolver.physical(a) == first
        assert stack.resolver.physical(c) == second

    def test_the_same_name_in_both_folders_keeps_one_location_per_folder(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)
        _put(library / "Lora" / "x.safetensors", b"first bytes")
        _put(library / "LyCORIS" / "x.safetensors", b"other bytes")

        stack.scanner.index_models(max_workers=1)

        model_id = _model_ids("x.safetensors")[0]
        rows = stack.scanner.locations.list_for_model(model_id)
        assert {r["rel_key"] for r in rows} == {"x.safetensors"}
        assert {r["binding_id"] for r in rows} == {
            _binding_id(stack, root["id"], "Lora"),
            _binding_id(stack, root["id"], "LyCORIS"),
        }
        assert sorted(r["status"] for r in rows) == ["conflict", "present"]

    def test_a_second_scan_judges_each_folder_by_its_own_rows(self, stack):
        library = _matrix(stack)
        _create(stack, library)
        _put(library / "Lora" / "x.safetensors", b"first bytes")
        _put(library / "LyCORIS" / "x.safetensors", b"other bytes")
        stack.scanner.index_models(max_workers=1)

        again = stack.scanner.index_models(max_workers=1)

        assert again["skipped"] == 1
        assert again["new_files"] == 1

    def test_the_folder_order_decides_which_copy_wins(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)
        first = _put(library / "Lora" / "x.safetensors", b"same bytes")
        second = _put(library / "LyCORIS" / "y.safetensors", b"same bytes")
        model = model_repo.create(Model(filename="x.safetensors", file_size=10, sha256="s", model_type="lora"))
        for binding_subdir, path in (("Lora", first), ("LyCORIS", second)):
            binding = stack.repository.find_binding(root["id"], "lora", binding_subdir)
            ModelLocationsRepository().upsert(
                model_id=model.id, binding_id=binding["id"], root_id=root["id"], model_type="lora",
                rel_path=path.name, rel_key=path.name, size=10, mtime_ns=None, sha256="s",
                status="present", seen_at=now_iso(),
            )
        assert stack.locator.path_for_model(model.id) == first

        order = [b["id"] for b in sorted(stack.repository.list_bindings("lora"), key=lambda b: b["position"])]
        ly = stack.repository.find_binding(root["id"], "lora", "LyCORIS")["id"]
        stack.repository.reorder_bindings("lora", [ly, *[i for i in order if i != ly]])
        stack.resolver.invalidate()

        assert stack.locator.path_for_model(model.id) == second

    def test_a_copy_in_the_second_folder_takes_over_when_the_first_is_gone(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)
        original = _put(library / "Lora" / "x.safetensors", b"same bytes")
        _put(library / "LyCORIS" / "x.safetensors", b"same bytes")
        stack.scanner.index_models(max_workers=1)
        model_id = _model_ids("x.safetensors")[0]
        assert stack.locator.path_for_model(model_id) == original

        original.unlink()
        stack.scanner.index_models(max_workers=1)

        assert stack.locator.path_for_model(model_id) == library / "LyCORIS" / "x.safetensors"
        assert stack.locator.path_for_ref("loras/x.safetensors") == library / "LyCORIS" / "x.safetensors"
        rows = {
            r["binding_id"]: r["status"] for r in stack.scanner.locations.list_for_model(model_id)
        }
        assert rows[_binding_id(stack, root["id"], "Lora")] == "missing"
        assert rows[_binding_id(stack, root["id"], "LyCORIS")] == "present"

    def test_a_missing_file_in_one_folder_does_not_hide_the_other_folder(self, stack):
        library = _matrix(stack)
        _create(stack, library)
        gone = _put(library / "Lora" / "a.safetensors", b"A")
        kept = _put(library / "LyCORIS" / "c.safetensors", b"C")
        stack.scanner.index_models(max_workers=1)

        gone.unlink()
        stack.scanner.index_models(max_workers=1)

        assert stack.locator.path_for_model(_model_ids("c.safetensors")[0]) == kept
        with pytest.raises(ModelFileUnavailable):
            stack.locator.path_for_model(_model_ids("a.safetensors")[0])

    def test_a_download_lands_in_the_write_folder_and_indexes_into_it(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)

        write_dir = stack.resolver.write_dir("lora")
        landed = _put(write_dir.path / "fresh.safetensors", b"downloaded")
        outcome = stack.scanner.index_single_model(str(landed), "lora")

        assert write_dir.path == library / "Lora"
        location = stack.scanner.locations.list_for_model(outcome.model.id)[0]
        assert location["binding_id"] == _binding_id(stack, root["id"], "Lora")
        assert stack.locator.path_for_model(outcome.model.id) == landed

    def test_the_write_folder_can_be_the_second_folder(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)

        stack.repository.set_write(root["id"], "lora", "LyCORIS")
        stack.resolver.invalidate()
        write_dir = stack.resolver.write_dir("lora")
        landed = _put(write_dir.path / "fresh.safetensors", b"downloaded")
        outcome = stack.scanner.index_single_model(str(landed), "lora")

        assert write_dir.path == library / "LyCORIS"
        assert [b["is_write"] for b in stack.repository.bindings_for(root["id"], "lora")] == [False, True]
        location = stack.scanner.locations.list_for_model(outcome.model.id)[0]
        assert location["binding_id"] == _binding_id(stack, root["id"], "LyCORIS")

    def test_removing_the_type_drops_both_folders_and_their_locations(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)
        _put(library / "Lora" / "a.safetensors", b"A")
        _put(library / "LyCORIS" / "c.safetensors", b"C")
        stack.scanner.index_models(max_workers=1)

        stack.manager.update_root(root["id"], remove_types=["lora"])

        assert stack.repository.bindings_for(root["id"], "lora") == []
        assert ModelLocationsRepository().list_for_root_type(root["id"], "lora") == []

    def test_scan_headers_is_set_per_folder(self, stack):
        library = stack.base / "forge"
        (library / "Stable-diffusion").mkdir(parents=True)
        (library / "checkpoints").mkdir(parents=True)
        root = stack.manager.create_root(
            str(library),
            bindings=[BindingSpec("checkpoint", "Stable-diffusion"), BindingSpec("checkpoint", "checkpoints")],
        )
        assert [b["scan_headers"] for b in root["bindings"]] == [True, False]

        updated = stack.manager.set_binding_scan_headers(root["id"], "checkpoint", "checkpoints", True)
        assert [b["scan_headers"] for b in updated["bindings"]] == [True, True]

        updated = stack.manager.set_binding_scan_headers(root["id"], "checkpoint", "Stable-diffusion", False)
        assert {b["subdir"]: b["scan_headers"] for b in updated["bindings"]} == {
            "Stable-diffusion": False, "checkpoints": True,
        }

    def test_reordering_roots_moves_all_of_a_roots_folders_together(self, stack):
        library = _matrix(stack)
        other = stack.base / "other"
        (other / "loras").mkdir(parents=True)
        matrix = _create(stack, library)
        second = stack.manager.create_root(str(other), bindings=[BindingSpec("lora", "loras")])

        stack.manager.reorder("lora", [matrix["id"], second["id"]])
        order = [b["subdir"] for b in sorted(stack.repository.list_bindings("lora"), key=lambda b: b["position"])]
        assert order.index("Lora") < order.index("LyCORIS") < order.index("loras")

        stack.manager.reorder("lora", [second["id"], matrix["id"]])
        order = [b["subdir"] for b in sorted(stack.repository.list_bindings("lora"), key=lambda b: b["position"])]
        assert order.index("loras") < order.index("Lora") < order.index("LyCORIS")

    def test_the_overview_lists_every_folder_of_a_type_in_order(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)

        overview = stack.manager.get_overview()

        lora = next(t for t in overview["types"] if t["model_type"] == "lora")
        mine = [b for b in lora["bindings"] if b["root_id"] == root["id"]]
        assert [b["subdir"] for b in mine] == ["Lora", "LyCORIS"]
        assert lora["order"].count(root["id"]) == 1


class TestNestingInvariant:

    def test_a_folder_inside_another_is_refused_on_create(self, stack):
        library = _matrix(stack)
        (library / "Lora" / "Styles").mkdir()

        with pytest.raises(BindingNestedError):
            stack.manager.create_root(
                str(library),
                bindings=[BindingSpec("lora", "Lora"), BindingSpec("lora", "Lora/Styles")],
            )

        assert stack.repository.get_root_by_path_key(str(library)) is None

    def test_folders_of_different_types_cannot_nest(self, stack):
        library = _matrix(stack)
        (library / "Lora" / "embeds").mkdir()

        with pytest.raises(BindingNestedError):
            stack.manager.create_root(
                str(library),
                bindings=[BindingSpec("lora", "Lora"), BindingSpec("embedding", "Lora/embeds")],
            )

    def test_one_folder_cannot_serve_two_types(self, stack):
        library = _matrix(stack)

        with pytest.raises(BindingNestedError):
            stack.manager.create_root(
                str(library),
                bindings=[BindingSpec("lora", "Lora"), BindingSpec("embedding", "Lora")],
            )

    def test_a_folder_listed_twice_is_a_duplicate(self, stack):
        library = _matrix(stack)

        with pytest.raises(DuplicateBindingError):
            stack.manager.create_root(
                str(library),
                bindings=[BindingSpec("lora", "Lora"), BindingSpec("lora", "Lora/")],
            )

    def test_a_symlink_to_a_bound_folder_is_a_duplicate_target(self, stack):
        library = _matrix(stack)
        try:
            (library / "LoraLink").symlink_to(library / "Lora", target_is_directory=True)
        except OSError:
            pytest.skip("symlinks are not available")

        with pytest.raises(BindingNestedError):
            stack.manager.create_root(
                str(library),
                bindings=[BindingSpec("lora", "Lora"), BindingSpec("lora", "LoraLink")],
            )

    def test_updating_a_root_refuses_a_nested_addition_and_adds_nothing(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)
        (library / "Lora" / "Styles").mkdir()

        with pytest.raises(BindingNestedError):
            stack.manager.update_root(root["id"], bindings=[BindingSpec("lora", "Lora/Styles")])

        assert [b["subdir"] for b in stack.repository.bindings_for(root["id"], "lora")] == ["Lora", "LyCORIS"]

    def test_updating_a_root_adds_a_sibling_folder(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)
        (library / "Locon").mkdir()

        updated = stack.manager.update_root(root["id"], bindings=[BindingSpec("lora", "Locon")])

        assert [b["subdir"] for b in updated["bindings"] if b["model_type"] == "lora"] == ["Lora", "LyCORIS", "Locon"]

    def test_updating_an_existing_folder_updates_it_in_place(self, stack):
        library = stack.base / "forge"
        (library / "Stable-diffusion").mkdir(parents=True)
        (library / "checkpoints").mkdir(parents=True)
        root = stack.manager.create_root(
            str(library),
            bindings=[BindingSpec("checkpoint", "Stable-diffusion"), BindingSpec("checkpoint", "checkpoints")],
        )
        before = stack.repository.find_binding(root["id"], "checkpoint", "checkpoints")

        updated = stack.manager.update_root(
            root["id"], bindings=[BindingSpec("checkpoint", "checkpoints", True)]
        )

        after = stack.repository.find_binding(root["id"], "checkpoint", "checkpoints")
        assert after["id"] == before["id"]
        assert (before["scan_headers"], after["scan_headers"]) == (0, 1)
        assert len([b for b in updated["bindings"] if b["model_type"] == "checkpoint"]) == 2

    def test_a_missing_folder_is_still_an_invalid_binding(self, stack):
        library = _matrix(stack)

        with pytest.raises(InvalidBindingError):
            stack.manager.create_root(str(library), bindings=[BindingSpec("lora", "Nope")])


class TestWriteFolderPerBinding:

    def _write_subdirs(self, stack, root_id):
        return [b["subdir"] for b in stack.repository.bindings_for(root_id, "lora") if b["is_write"]]

    def test_the_write_flag_picks_the_folder_on_create(self, stack):
        library = _matrix(stack)

        root = stack.manager.create_root(
            str(library),
            bindings=[BindingSpec("lora", "Lora"), BindingSpec("lora", "LyCORIS", None, True)],
        )

        assert self._write_subdirs(stack, root["id"]) == ["LyCORIS"]
        assert stack.resolver.write_dir("lora").path == library / "LyCORIS"

    def test_write_types_alone_picks_the_first_folder_of_the_type(self, stack):
        library = _matrix(stack)

        root = _create(stack, library)

        assert self._write_subdirs(stack, root["id"]) == ["Lora"]

    def test_write_types_and_a_flag_agree_on_the_flagged_folder(self, stack):
        library = _matrix(stack)

        root = stack.manager.create_root(
            str(library),
            bindings=[BindingSpec("lora", "Lora"), BindingSpec("lora", "LyCORIS", None, True)],
            write_types=["lora"],
        )

        assert self._write_subdirs(stack, root["id"]) == ["LyCORIS"]

    def test_two_flagged_folders_of_one_type_are_refused(self, stack):
        library = _matrix(stack)

        with pytest.raises(InvalidBindingError):
            stack.manager.create_root(
                str(library),
                bindings=[BindingSpec("lora", "Lora", None, True), BindingSpec("lora", "LyCORIS", None, True)],
            )

        assert stack.repository.get_root_by_path_key(str(library)) is None

    def test_a_flag_on_a_read_only_root_is_refused(self, stack):
        library = _matrix(stack)

        with pytest.raises(RootReadOnlyRefusalError):
            stack.manager.create_root(
                str(library), bindings=[BindingSpec("lora", "Lora", None, True)], read_only=True
            )

    def test_updating_with_a_flag_moves_the_write_folder(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)

        stack.manager.update_root(root["id"], bindings=[BindingSpec("lora", "LyCORIS", None, True)])

        assert self._write_subdirs(stack, root["id"]) == ["LyCORIS"]

    def test_updating_can_add_a_folder_and_make_it_the_write_folder(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)
        (library / "Locon").mkdir()

        stack.manager.update_root(root["id"], bindings=[BindingSpec("lora", "Locon", None, True)])

        assert self._write_subdirs(stack, root["id"]) == ["Locon"]

    def test_a_flag_on_a_read_only_root_is_refused_on_update(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)
        stack.manager.update_root(root["id"], read_only=True)

        with pytest.raises(RootReadOnlyRefusalError):
            stack.manager.update_root(root["id"], bindings=[BindingSpec("lora", "LyCORIS", None, True)])

    def test_set_write_root_takes_a_subdir(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)

        stack.manager.set_write_root("lora", root["id"], "LyCORIS")

        assert self._write_subdirs(stack, root["id"]) == ["LyCORIS"]
        assert stack.resolver.write_dir("lora").path == library / "LyCORIS"

    def test_set_write_root_without_a_subdir_uses_the_first_folder(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)
        stack.manager.set_write_root("lora", root["id"], "LyCORIS")

        stack.manager.set_write_root("lora", root["id"])

        assert self._write_subdirs(stack, root["id"]) == ["Lora"]

    def test_set_write_root_refuses_a_subdir_the_root_does_not_bind(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)

        with pytest.raises(InvalidBindingError):
            stack.manager.set_write_root("lora", root["id"], "Nope")
        with pytest.raises(InvalidBindingError):
            stack.manager.set_write_root(None, root["id"], "Lora")


class TestRemoveBindings:

    def test_removing_one_folder_keeps_its_sibling_and_its_files(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)
        _put(library / "Lora" / "a.safetensors", b"A")
        kept = _put(library / "LyCORIS" / "c.safetensors", b"C")
        stack.scanner.index_models(max_workers=1)

        updated = stack.manager.update_root(root["id"], remove_bindings=[BindingRef("lora", "Lora")])

        assert [b["subdir"] for b in updated["bindings"] if b["model_type"] == "lora"] == ["LyCORIS"]
        assert stack.locator.path_for_model(_model_ids("c.safetensors")[0]) == kept
        with pytest.raises(ModelFileUnavailable):
            stack.locator.path_for_model(_model_ids("a.safetensors")[0])

    def test_removing_the_write_folder_hands_writes_to_the_sibling(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)

        stack.manager.update_root(root["id"], remove_bindings=[BindingRef("lora", "Lora")])

        assert stack.resolver.write_dir("lora").path == library / "LyCORIS"

    def test_removing_the_only_write_folder_hands_writes_to_another_root(self, stack):
        library = _matrix(stack)
        root = stack.manager.create_root(
            str(library), bindings=[BindingSpec("lora", "Lora")], write_types=["lora"]
        )

        stack.manager.update_root(root["id"], remove_bindings=[BindingRef("lora", "Lora")])

        assert stack.repository.bindings_for(root["id"], "lora") == []
        assert [b["root_id"] for b in stack.repository.list_bindings("lora") if b["is_write"]] == ["home"]

    def test_an_unknown_folder_is_refused_and_nothing_is_removed(self, stack):
        library = _matrix(stack)
        root = _create(stack, library)

        with pytest.raises(InvalidBindingError):
            stack.manager.update_root(
                root["id"], remove_bindings=[BindingRef("lora", "Lora"), BindingRef("lora", "Nope")]
            )

        assert len(stack.repository.bindings_for(root["id"], "lora")) == 2

    def test_the_home_root_cannot_lose_folders(self, stack):
        from src.features.models.roots import HomeProtectedError

        with pytest.raises(HomeProtectedError):
            stack.manager.update_root("home", remove_bindings=[BindingRef("lora", "loras")])
