from unittest.mock import MagicMock

from src.features.models.indexing_coordinator import ModelIndexingCoordinator
from tests.features.models.test_indexing_coordinator import FakePluginRegistry
from tests.fixtures.model_index_fixtures import FLUX, LORA, Library


def _coordinator(lib):
    return ModelIndexingCoordinator(
        spawn=lambda target: target(),
        model_repository=MagicMock(),
        plugin_registry=FakePluginRegistry(),
        scanner=lib.scanner,
        backend_registry=None,
        native_availability_projector=None,
    )


def test_status_counts_models_that_need_a_type_and_those_classified_from_headers(tmp_path, threadsafe_db):
    lib = Library(tmp_path)
    lib.put(lib.checkpoints, "flux.safetensors", FLUX)
    lib.put(lib.checkpoints, "mystery.safetensors", LORA)
    lib.put(lib.diffusion, "plain.safetensors", LORA, tag="plain")
    coordinator = _coordinator(lib)

    coordinator.start_indexing()
    status = coordinator.status()

    assert status["needs_type_total"] == 1
    assert status["classified_total"] == 2
    assert status["state"] == "done"


def test_blocked_retypes_are_listed_with_the_other_conflicts(tmp_path, threadsafe_db):
    lib = Library(tmp_path, scan_checkpoints=False)
    lib.put(lib.checkpoints, "flux.safetensors", FLUX, tag="mine")
    lib.put(lib.diffusion, "flux.safetensors", LORA, tag="other")
    coordinator = _coordinator(lib)
    coordinator.start_indexing()
    assert coordinator.status()["conflicts"] == []

    lib.set_scan("checkpoint", True)
    coordinator.scanner = lib.scanner
    coordinator.start_indexing()

    conflicts = coordinator.status()["conflicts"]
    assert [c["status"] for c in conflicts] == ["type_conflict"]
    assert "type change blocked" in conflicts[0]["message"]


def test_a_classify_only_run_counts_toward_processed(tmp_path, threadsafe_db):
    lib = Library(tmp_path, scan_checkpoints=False)
    lib.put(lib.checkpoints, "flux.safetensors", FLUX)
    coordinator = _coordinator(lib)
    coordinator.start_indexing()
    lib.set_scan("checkpoint", True)
    coordinator.scanner = lib.scanner

    assert coordinator.count_unindexed()["total"] == 1
    assert coordinator.resume_interrupted_indexing() is True

    status = coordinator.status()
    assert status["processed"] == 1
    assert coordinator.count_unindexed()["total"] == 0
