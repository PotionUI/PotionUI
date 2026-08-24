"""GenerationStatsManager tests: preset-name resolution at write time
and pass-through reads. The repository is mocked -- SQL correctness is
covered by test_generation_stats_repository.py.
"""

from unittest.mock import Mock

import pytest

from src.features.stats.generation_stats_manager import GenerationStatsManager


@pytest.fixture
def mock_repo():
    return Mock()


@pytest.fixture
def mock_file_preset_repository():
    return Mock()


@pytest.fixture
def manager(mock_repo, mock_file_preset_repository):
    return GenerationStatsManager(
        generation_stats_repository=mock_repo,
        file_preset_repository=mock_file_preset_repository,
    )


class TestRecordCompletion:
    def test_resolves_preset_name_from_disk(self, manager, mock_repo, mock_file_preset_repository):
        template = Mock()
        template.name = "SDXL Base (Realistic)"
        mock_file_preset_repository.find_preset_by_id.return_value = template

        manager.record_completion(
            generation_id="g1", preset_id="native/SDXL/base", engine="native",
            backend_id="b1", duration_ms=5000, cold_start=True, model_load_ms=1000.0,
            peak_vram_mb=8000.0, peak_ram_mb=12000.0, cpu_percent=50.0,
        )

        mock_file_preset_repository.find_preset_by_id.assert_called_once_with("native/SDXL/base")
        _, kwargs = mock_repo.record_completion.call_args
        assert kwargs["preset_name"] == "SDXL Base (Realistic)"
        assert kwargs["generation_id"] == "g1"
        assert kwargs["cold_start"] is True

    def test_falls_back_to_preset_id_when_preset_deleted_from_disk(
        self, manager, mock_repo, mock_file_preset_repository
    ):
        """The whole point of storing preset_name is that it survives the
        preset being deleted -- so a missing preset at WRITE time still must
        not leave the row nameless."""
        mock_file_preset_repository.find_preset_by_id.return_value = None

        manager.record_completion(
            generation_id="g1", preset_id="native/SDXL/gone", engine="native",
            backend_id="b1", duration_ms=1000, cold_start=None, model_load_ms=None,
            peak_vram_mb=None, peak_ram_mb=None, cpu_percent=None,
        )

        _, kwargs = mock_repo.record_completion.call_args
        assert kwargs["preset_name"] == "native/SDXL/gone"

    def test_malformed_preset_lookup_does_not_block_the_write(
        self, manager, mock_repo, mock_file_preset_repository
    ):
        mock_file_preset_repository.find_preset_by_id.side_effect = Exception("bad yaml")

        manager.record_completion(
            generation_id="g1", preset_id="broken/preset", engine="native",
            backend_id="b1", duration_ms=1000, cold_start=False, model_load_ms=None,
            peak_vram_mb=None, peak_ram_mb=None, cpu_percent=None,
        )

        mock_repo.record_completion.assert_called_once()

    def test_no_preset_id_skips_lookup(self, manager, mock_repo, mock_file_preset_repository):
        manager.record_completion(
            generation_id="g1", preset_id=None, engine=None, backend_id=None,
            duration_ms=1000, cold_start=None, model_load_ms=None,
            peak_vram_mb=None, peak_ram_mb=None, cpu_percent=None,
        )
        mock_file_preset_repository.find_preset_by_id.assert_not_called()

    def test_repository_failure_is_swallowed(self, manager, mock_repo, mock_file_preset_repository):
        """A stats write must never be able to raise into the caller (the
        generation completion path) -- see GenerationOrchestrator._finish_generation,
        which also wraps its own call in a try/except, but this method's own
        contract must hold independently."""
        mock_file_preset_repository.find_preset_by_id.return_value = None
        mock_repo.record_completion.side_effect = Exception("db is locked")

        manager.record_completion(
            generation_id="g1", preset_id="p1", engine="native", backend_id="b1",
            duration_ms=1000, cold_start=True, model_load_ms=None,
            peak_vram_mb=None, peak_ram_mb=None, cpu_percent=None,
        )  # must not raise

    # Reads (preset_timing/preset_resources) go straight from the controller
    # to GenerationStatsRepository - the manager has no read-side logic left
    # to cover.
