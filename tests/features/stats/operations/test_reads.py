import pytest
from unittest.mock import Mock

from src.features.presets.file_repository import FilePresetRepository
from src.features.stats import operations
from src.features.stats.repository import StatsRepository


class TestBreakdown:
    @pytest.fixture
    def mock_repo(self):
        repo = Mock(spec=StatsRepository)
        repo.breakdown.return_value = {
            'dimension': 'preset',
            'items': [{'key': '01ABC', 'group': None, 'count': 3}],
            'total_distinct': 1,
        }
        repo.timeseries.return_value = [{'bucket': '2026-01-01', 'value': 1}]
        return repo

    @pytest.fixture
    def mock_presets(self):
        repo = Mock(spec=FilePresetRepository)
        repo.list_all_presets.return_value = [{'id': '01ABC', 'name': '[Local/SDXL] T2I'}]
        return repo

    def test_preset_ids_get_display_names(self, mock_repo, mock_presets):
        items = operations.breakdown(mock_repo, mock_presets, 'preset')['items']
        assert items[0]['label'] == '[Local/SDXL] T2I'

    def test_unknown_preset_falls_back_to_its_id(self, mock_repo, mock_presets):
        mock_presets.list_all_presets.return_value = []
        items = operations.breakdown(mock_repo, mock_presets, 'preset')['items']
        assert items[0]['label'] == '01ABC'

    def test_a_broken_preset_on_disk_does_not_break_stats(self, mock_repo, mock_presets):
        mock_presets.list_all_presets.side_effect = OSError("bad yaml")
        items = operations.breakdown(mock_repo, mock_presets, 'preset')['items']
        assert items[0]['label'] == '01ABC'

    def test_non_preset_dimensions_label_with_their_key(self, mock_repo, mock_presets):
        mock_repo.breakdown.return_value = {
            'dimension': 'sampler',
            'items': [{'key': 'euler', 'group': None, 'count': 9}],
            'total_distinct': 1,
        }
        items = operations.breakdown(mock_repo, mock_presets, 'sampler')['items']
        assert items[0]['label'] == 'euler'

    def test_preset_names_are_not_loaded_for_other_dimensions(self, mock_repo, mock_presets):
        mock_repo.breakdown.return_value = {'dimension': 'sampler', 'items': [], 'total_distinct': 0}
        operations.breakdown(mock_repo, mock_presets, 'sampler')
        mock_presets.list_all_presets.assert_not_called()

    # --- metric/bucket/dimension/limit validation happens at the route's Query
    # types (stats/routes.py's MetricParam/BucketParam/DimensionParam and the
    # ge/le bounds) - by the time a call reaches this operation, the value is
    # already known-good, so there is nothing left for it to check.

    def test_timeseries_wraps_points_with_metadata(self, mock_repo, mock_presets):
        result = operations.timeseries(mock_repo, metric='count', bucket='day')
        assert result == {'metric': 'count', 'bucket': 'day', 'points': [{'bucket': '2026-01-01', 'value': 1}]}
