from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.features.models.indexer import ModelScanner
from src.features.models.repository import model_repo
from src.features.models.type_repository import ModelTypeRepository
from src.platform.database.rows import now_iso
from src.platform.runtime.model_headers import read_header as real_read_header
from tests.fixtures.model_header_fixtures import tensor_specs, write_safetensors
from tests.fixtures.model_index_fixtures import (
    FLUX,
    LORA,
    SDXL_ALL_IN_ONE,
    Library,
    add_binding,
    add_root,
    make_resolver,
    sha_of,
)

@pytest.fixture
def lib(tmp_path, mock_db):
    return Library(tmp_path)


def test_flux_transformer_in_a_scanned_folder_becomes_a_diffusion_model(lib):
    lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)

    result = lib.index()

    model = lib.model("flux-dev.safetensors")
    assert model.model_type == "diffusion_model"
    assert model.type_source == "header"
    assert result["models"][0]["model_type"] == "diffusion_model"
    locations = lib.scanner.locations.list_for_model(model.id)
    assert [(loc["model_type"], loc["status"]) for loc in locations] == [("checkpoint", "present")]


def test_sdxl_all_in_one_stays_a_checkpoint(lib):
    lib.put(lib.checkpoints, "sdxl.safetensors", SDXL_ALL_IN_ONE)
    lib.index()
    model = lib.model("sdxl.safetensors")
    assert (model.model_type, model.type_source) == ("checkpoint", "header")


def test_an_unrecognised_file_becomes_undefined(lib):
    lib.put(lib.checkpoints, "mystery.safetensors", LORA)
    lib.index()
    model = lib.model("mystery.safetensors")
    assert (model.model_type, model.type_source) == ("undefined", "header")
    assert lib.scanner.get_indexing_status()["by_type"]["undefined"]["count"] == 1
    assert lib.scanner.types.count_needs_type() == 1


def test_undefined_models_are_hidden_from_untyped_listings(lib):
    lib.put(lib.checkpoints, "mystery.safetensors", LORA)
    lib.put(lib.checkpoints, "sdxl.safetensors", SDXL_ALL_IN_ONE)
    lib.index()

    names = {m.filename for m in model_repo.get_all(include_providers=False, include_tags=False)}
    assert names == {"sdxl.safetensors"}
    everything = model_repo.get_all(include_providers=False, include_tags=False, include_undefined=True)
    assert {m.filename for m in everything} == {"sdxl.safetensors", "mystery.safetensors"}
    only = model_repo.get_all(model_type="undefined", include_providers=False, include_tags=False)
    assert [m.filename for m in only] == ["mystery.safetensors"]


def test_pickle_files_are_never_opened_for_classification(lib):
    (lib.checkpoints / "old.ckpt").write_bytes(b"pickle bytes")
    with patch("src.features.models.indexer.read_header") as spy:
        lib.index()
    spy.assert_not_called()
    model = lib.model("old.ckpt")
    assert (model.model_type, model.type_source) == ("checkpoint", "folder")


def test_unscanned_folders_never_read_headers(tmp_path, mock_db):
    lib = Library(tmp_path, scan_checkpoints=False)
    lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    with patch("src.features.models.indexer.read_header") as spy:
        lib.index()
    spy.assert_not_called()
    assert lib.model("flux-dev.safetensors").model_type == "checkpoint"


def test_rescanning_unchanged_files_reads_no_header_and_no_bytes(lib):
    lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    lib.put(lib.checkpoints, "mystery.safetensors", LORA)
    lib.index()

    with patch("src.features.models.indexer.read_header") as header_spy, patch.object(
        ModelScanner, "calculate_sha256", side_effect=AssertionError("must not rehash")
    ):
        result = lib.index()

    header_spy.assert_not_called()
    assert result["new_files"] == 0
    assert lib.model("flux-dev.safetensors").model_type == "diffusion_model"


def test_new_files_are_classified_from_the_hash_stream_without_a_second_read(lib):
    path = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    seen = {}

    def spy(file_path, *, prefix=None):
        seen["prefix"] = prefix
        return real_read_header(file_path, prefix=prefix)

    with patch("src.features.models.indexer.read_header", side_effect=spy):
        lib.index()

    assert seen["prefix"] and path.read_bytes().startswith(seen["prefix"])
    assert lib.model("flux-dev.safetensors").model_type == "diffusion_model"


def test_verdict_is_stored_once_per_sha_and_reused_by_a_second_copy(lib):
    lib.put(lib.checkpoints, "flux-a.safetensors", FLUX, tag="same")
    content = (lib.checkpoints / "flux-a.safetensors").read_bytes()
    (lib.checkpoints / "sub").mkdir()
    (lib.checkpoints / "sub" / "flux-b.safetensors").write_bytes(content)

    with patch("src.features.models.indexer.read_header", side_effect=real_read_header) as spy:
        lib.index()

    assert spy.call_count == 1


def test_invalid_headers_are_cached_and_fall_back_to_the_folder_type(lib):
    (lib.checkpoints / "broken.safetensors").write_bytes(b"\x01\x00\x00\x00\x00\x00\x00\x00{")
    lib.index()
    model = lib.model("broken.safetensors")
    assert (model.model_type, model.type_source) == ("checkpoint", "folder")
    verdict = ModelTypeRepository().get_verdicts([model.sha256])[model.sha256]
    assert verdict["status"] == "invalid"


def test_backfill_classifies_existing_rows_without_rehashing(tmp_path, mock_db):
    lib = Library(tmp_path, scan_checkpoints=False)
    lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    lib.index()
    assert lib.model("flux-dev.safetensors").model_type == "checkpoint"

    lib.set_scan("checkpoint", True)
    assert lib.scanner.count_unindexed()["total"] == 1

    with patch.object(ModelScanner, "calculate_sha256", side_effect=AssertionError("must not rehash")):
        result = lib.index()

    model = lib.model("flux-dev.safetensors")
    assert (model.model_type, model.type_source) == ("diffusion_model", "header")
    assert result["classified"] == 1
    assert result["new_files"] == 0
    assert lib.scanner.count_unindexed()["total"] == 0


def test_backfill_reports_reading_headers_and_runs_before_hashing(tmp_path, mock_db):
    lib = Library(tmp_path, scan_checkpoints=False)
    lib.put(lib.checkpoints, "flux-old.safetensors", FLUX, tag="old")
    lib.index()
    lib.set_scan("checkpoint", True)
    lib.put(lib.checkpoints, "flux-new.safetensors", FLUX, tag="new")

    order = []
    messages = []
    lib.scanner.set_progress_callback(lambda current, total, message: messages.append((current, total, message)))
    real_hash = ModelScanner.calculate_sha256

    def hash_spy(self, path, *args, **kwargs):
        order.append(("hash", Path(path).name))
        return real_hash(self, path, *args, **kwargs)

    def header_spy(path, *, prefix=None):
        order.append(("header", Path(path).name))
        return real_read_header(path, prefix=prefix)

    with patch.object(ModelScanner, "calculate_sha256", hash_spy), patch(
        "src.features.models.indexer.read_header", side_effect=header_spy
    ):
        lib.index()

    assert order.index(("header", "flux-old.safetensors")) < order.index(("hash", "flux-new.safetensors"))
    assert any(message == "Reading file headers..." for _, _, message in messages)
    first = [m for m in messages if m[1]][0]
    assert (first[1], first[2]) == (2, "Reading file headers...")


def test_toggling_a_folder_off_reverts_the_type_with_no_io(lib):
    lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    lib.index()
    assert lib.model("flux-dev.safetensors").model_type == "diffusion_model"

    lib.set_scan("checkpoint", False)
    with patch("src.features.models.indexer.read_header") as header_spy, patch.object(
        ModelScanner, "calculate_sha256", side_effect=AssertionError("must not rehash")
    ), patch("builtins.open", side_effect=AssertionError("must not open files")):
        lib.index()

    header_spy.assert_not_called()
    model = lib.model("flux-dev.safetensors")
    assert (model.model_type, model.type_source) == ("checkpoint", "folder")


def test_toggling_a_folder_on_reuses_a_stored_verdict_without_reading(lib):
    lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    lib.index()
    lib.set_scan("checkpoint", False)
    lib.index()
    assert lib.model("flux-dev.safetensors").model_type == "checkpoint"

    lib.set_scan("checkpoint", True)
    with patch("src.features.models.indexer.read_header") as header_spy:
        lib.index()
    header_spy.assert_not_called()
    assert lib.model("flux-dev.safetensors").model_type == "diffusion_model"


def test_same_bytes_in_a_scanned_and_an_unscanned_folder_are_one_model_two_locations(lib):
    first = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    (lib.diffusion / "flux-dev.safetensors").write_bytes(first.read_bytes())

    result = lib.index()

    model = lib.model("flux-dev.safetensors")
    assert model.model_type == "diffusion_model"
    locations = lib.scanner.locations.list_for_model(model.id)
    assert {(loc["model_type"], loc["status"]) for loc in locations} == {
        ("checkpoint", "present"),
        ("diffusion_model", "present"),
    }
    assert result["skipped_duplicates"] == []


def test_same_bytes_with_another_filename_is_still_a_skipped_duplicate(lib):
    first = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    (lib.checkpoints / "flux-copy.safetensors").write_bytes(first.read_bytes())

    result = lib.index()

    assert len(result["skipped_duplicates"]) == 1
    assert len(model_repo.get_all(include_providers=False, include_tags=False)) == 1


def test_disagreeing_unscanned_copies_are_settled_by_reading_the_header(tmp_path, mock_db):
    lib = Library(tmp_path, scan_checkpoints=False, scan_diffusion=False)
    first = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    (lib.diffusion / "flux-dev.safetensors").write_bytes(first.read_bytes())

    with patch("src.features.models.indexer.read_header", side_effect=real_read_header) as spy:
        lib.index()

    model = lib.model("flux-dev.safetensors")
    assert (model.model_type, model.type_source) == ("diffusion_model", "header")
    assert spy.call_count == 1


def test_disagreeing_unscanned_copies_with_an_undecided_header_keep_the_first_folder_type(tmp_path, mock_db):
    lib = Library(tmp_path, scan_checkpoints=False, scan_diffusion=False)
    first = lib.put(lib.checkpoints, "thing.safetensors", LORA)
    (lib.diffusion / "thing.safetensors").write_bytes(first.read_bytes())

    lib.index()

    model = lib.model("thing.safetensors")
    assert (model.model_type, model.type_source) == ("checkpoint", "folder")


def test_agreeing_unscanned_copies_are_never_read(tmp_path, mock_db):
    lib = Library(tmp_path, scan_checkpoints=False, scan_diffusion=False)
    first = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    (lib.checkpoints / "sub").mkdir()
    (lib.checkpoints / "sub" / "flux-dev.safetensors").write_bytes(first.read_bytes())

    with patch("src.features.models.indexer.read_header") as spy:
        lib.index()

    spy.assert_not_called()
    assert lib.model("flux-dev.safetensors").model_type == "checkpoint"


def test_assertions_beat_the_header_and_reset_falls_back_to_it(lib):
    path = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    types = ModelTypeRepository()
    types.put_assertion(sha_of(path), "checkpoint", "admin", None, now_iso())

    lib.index()
    model = lib.model("flux-dev.safetensors")
    assert (model.model_type, model.type_source) == ("checkpoint", "admin")

    types.delete_assertion(sha_of(path))
    lib.index()
    model = lib.model("flux-dev.safetensors")
    assert (model.model_type, model.type_source) == ("diffusion_model", "header")


@pytest.mark.parametrize("source", ["admin", "recipe", "download"])
def test_every_assertion_source_is_recorded_as_the_type_source(lib, source):
    path = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    ModelTypeRepository().put_assertion(sha_of(path), "checkpoint", source, None, now_iso())
    lib.index()
    assert lib.model("flux-dev.safetensors").type_source == source


def test_an_assertion_applies_again_when_the_file_comes_back(lib):
    path = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    sha = sha_of(path)
    ModelTypeRepository().put_assertion(sha, "checkpoint", "admin", None, now_iso())
    lib.index()
    model_repo.delete(lib.model("flux-dev.safetensors").id)
    lib.scanner.locations.delete_missing_for_roots(["r1"])

    lib.index()

    assert lib.model("flux-dev.safetensors").type_source == "admin"


def test_a_retype_that_would_collide_is_blocked_and_reported(tmp_path, mock_db):
    lib = Library(tmp_path, scan_checkpoints=False)
    mine = lib.put(lib.checkpoints, "flux.safetensors", FLUX, tag="mine")
    other = lib.put(lib.diffusion, "flux.safetensors", tensor_specs({"other.weight": (4, 4)}), tag="other")
    lib.index()
    assert lib.model("flux.safetensors", "checkpoint").sha256 == sha_of(mine)
    assert lib.model("flux.safetensors", "diffusion_model").sha256 == sha_of(other)

    lib.set_scan("checkpoint", True)
    result = lib.index()

    assert lib.model("flux.safetensors", "checkpoint").sha256 == sha_of(mine)
    assert len(result["type_conflicts"]) == 1
    conflict = result["type_conflicts"][0]
    assert conflict["model_type"] == "diffusion_model"
    assert "type change blocked" in conflict["message"]
    assert "flux.safetensors" in conflict["message"]


def test_directory_models_are_left_alone(tmp_path, mock_db):
    base = tmp_path / "lib"
    llm_dir = base / "llm" / "chat-model"
    llm_dir.mkdir(parents=True)
    (llm_dir / "config.json").write_text("{}")
    (llm_dir / "model.safetensors").write_bytes(b"weights")
    root = add_root("r1", base)
    binding = add_binding("r1", "llm", base / "llm", 0, scan_headers=True)
    scanner = ModelScanner(make_resolver([root], [binding]))

    with patch("src.features.models.indexer.read_header") as spy:
        scanner.index_models(max_workers=1)

    spy.assert_not_called()
    assert model_repo.get_by_filename("chat-model")[0].model_type == "llm"


def test_index_single_model_classifies_a_download_in_a_scanned_folder(lib):
    path = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)

    outcome = lib.scanner.index_single_model(str(path), "checkpoint")

    assert outcome.model.model_type == "diffusion_model"
    assert lib.model("flux-dev.safetensors").type_source == "header"


def test_a_changed_file_is_reclassified_under_its_new_hash(lib):
    path = lib.put(lib.checkpoints, "thing.safetensors", LORA)
    lib.index()
    assert lib.model("thing.safetensors").model_type == "undefined"

    lib.put(lib.checkpoints, "thing.safetensors", FLUX, tag="now a transformer")
    lib.index()

    assert lib.model("thing.safetensors").model_type == "diffusion_model"
    assert lib.model("thing.safetensors").sha256 == sha_of(path)


def test_recompute_scales_to_a_large_library_with_batched_queries(lib):
    for index in range(150):
        lib.put(lib.checkpoints, f"m{index}.safetensors", SDXL_ALL_IN_ONE, tag=str(index))
    lib.index()
    ids = {m.id for m in model_repo.get_all(include_providers=False, include_tags=False)}
    assert len(ids) == 150

    from src.platform.database import database

    counted = {"n": 0}
    original = database.db.get_cursor

    from contextlib import contextmanager

    @contextmanager
    def counting():
        counted["n"] += 1
        with original() as cursor:
            yield cursor

    with patch.object(database.db, "get_cursor", counting):
        lib.scanner.recompute_types(ids)

    assert counted["n"] <= 12


def test_parallel_workers_classify_every_file_once(tmp_path, threadsafe_db):
    lib = Library(tmp_path)
    for index in range(24):
        lib.put(lib.checkpoints, f"m{index}.safetensors", FLUX if index % 2 else SDXL_ALL_IN_ONE, tag=str(index))

    with patch("src.features.models.indexer.read_header", side_effect=real_read_header) as spy:
        result = lib.scanner.index_models(max_workers=4)

    assert result["failed"] == 0
    assert spy.call_count == 24
    types = {}
    for model in model_repo.get_all(include_providers=False, include_tags=False, include_undefined=True):
        types[model.model_type] = types.get(model.model_type, 0) + 1
    assert types == {"diffusion_model": 12, "checkpoint": 12}


def test_a_directory_is_never_classifiable_even_with_a_header_extension():
    from src.features.models.indexer import FoundFile

    directory = FoundFile("r1", "checkpoint", "x.gguf", "/x/x.gguf", 1, 1, True, True)
    plain = FoundFile("r1", "checkpoint", "x.gguf", "/x/x.gguf", 1, 1, False, True)
    unscanned = FoundFile("r1", "checkpoint", "x.gguf", "/x/x.gguf", 1, 1, False, False)
    pickle = FoundFile("r1", "checkpoint", "x.ckpt", "/x/x.ckpt", 1, 1, False, True)

    assert (directory.classifiable, plain.classifiable, unscanned.classifiable, pickle.classifiable) == (
        False, True, False, False,
    )


def test_a_file_that_changed_since_it_was_indexed_is_not_classified_under_its_old_hash(tmp_path, mock_db):
    lib = Library(tmp_path, scan_checkpoints=False)
    path = lib.put(lib.checkpoints, "thing.safetensors", LORA)
    lib.index()
    old_sha = sha_of(path)
    lib.put(lib.checkpoints, "thing.safetensors", FLUX, tag="replaced")
    lib.set_scan("checkpoint", True)

    lib.index()

    assert ModelTypeRepository().get_verdicts([old_sha]) == {}
    assert lib.model("thing.safetensors").model_type == "diffusion_model"


def test_the_outcome_of_an_index_call_reflects_a_retype_caused_by_it(tmp_path, mock_db):
    lib = Library(tmp_path, scan_checkpoints=False, scan_diffusion=True)
    first = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    (lib.diffusion / "flux-dev.safetensors").write_bytes(first.read_bytes())

    result = lib.index()

    assert result["models"][-1]["model_type"] == "diffusion_model"


def test_a_missing_scanned_copy_no_longer_decides_the_type(tmp_path, mock_db):
    lib = Library(tmp_path, scan_checkpoints=False, scan_diffusion=True)
    first = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    scanned = lib.diffusion / "flux-dev.safetensors"
    scanned.write_bytes(first.read_bytes())
    lib.index()
    assert lib.model("flux-dev.safetensors").model_type == "diffusion_model"

    scanned.unlink()
    lib.index()

    model = lib.model("flux-dev.safetensors")
    assert (model.model_type, model.type_source) == ("checkpoint", "folder")


def test_the_type_source_round_trips_through_the_repository(mock_db):
    from src.features.models.records import Model

    created = model_repo.create(
        Model(filename="a.safetensors", model_type="lora", sha256="a" * 64, type_source="recipe")
    )
    assert model_repo.get_by_id(created.id).type_source == "recipe"

    created.type_source = "header"
    model_repo.update(created)

    assert model_repo.get_by_id(created.id).type_source == "header"


def test_an_unreadable_header_is_reported_not_cached_and_retried_on_the_next_run(lib):
    from src.platform.runtime.model_headers import HeaderResult, HeaderStatus

    path = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    with patch(
        "src.features.models.indexer.read_header", return_value=HeaderResult(HeaderStatus.IO_ERROR, error="denied")
    ):
        first = lib.index()
    model = lib.model("flux-dev.safetensors")
    assert (model.model_type, model.type_source) == ("checkpoint", "folder")
    assert ModelTypeRepository().get_verdicts([model.sha256]) == {}
    assert first["indexed"] == 1
    assert first["failed_files"] == [{"path": str(path), "error": "Could not read the file header: denied"}]
    assert first["failed_by_root"] == {"r1": 1}
    assert lib.scanner.count_unindexed()["total"] == 1

    second = lib.index()

    assert second["classified"] == 1
    assert second["failed_files"] == []
    assert lib.model("flux-dev.safetensors").model_type == "diffusion_model"


def test_a_classify_only_job_that_cannot_read_is_reported(tmp_path, mock_db):
    from src.platform.runtime.model_headers import HeaderResult, HeaderStatus

    lib = Library(tmp_path, scan_checkpoints=False)
    path = lib.put(lib.checkpoints, "flux-dev.safetensors", FLUX)
    lib.index()
    lib.set_scan("checkpoint", True)

    with patch(
        "src.features.models.indexer.read_header", return_value=HeaderResult(HeaderStatus.IO_ERROR, error="denied")
    ):
        result = lib.index()

    assert result["classified"] == 0
    assert result["failed_files"] == [{"path": str(path), "error": "Could not read the file header: denied"}]
    assert result["failed_by_root"] == {"r1": 1}
    assert lib.index()["classified"] == 1


def test_single_file_indexing_returns_its_conflicts_on_the_outcome_and_keeps_no_state(tmp_path, mock_db):
    lib = Library(tmp_path, scan_checkpoints=False)
    mine = lib.put(lib.checkpoints, "flux.safetensors", FLUX, tag="mine")
    lib.put(lib.diffusion, "flux.safetensors", LORA, tag="other")
    lib.index()
    lib.set_scan("checkpoint", True)

    outcome = lib.scanner.index_single_model(str(mine), "checkpoint")

    assert [c["status"] for c in outcome.type_conflicts] == ["type_conflict"]
    assert not hasattr(lib.scanner, "_type_conflicts")


def test_a_full_scan_reports_a_conflict_found_while_indexing_a_changed_file_once(tmp_path, mock_db):
    lib = Library(tmp_path, scan_checkpoints=False)
    lib.put(lib.checkpoints, "flux.safetensors", FLUX, tag="mine")
    lib.put(lib.diffusion, "flux.safetensors", LORA, tag="other")
    lib.index()
    lib.set_scan("checkpoint", True)
    lib.put(lib.checkpoints, "flux.safetensors", FLUX, tag="mine, edited")

    result = lib.index()

    assert [c["status"] for c in result["type_conflicts"]] == ["type_conflict"]
