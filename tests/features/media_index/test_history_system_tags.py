"""System tags in history payloads and the system_tag facet filter."""

import importlib

from tests.features.media_index.test_repository import MediaIndexTestBase

from src.features.generation.history_query import GenerationHistoryQuery
from src.features.generation.repository import GenerationRepository
from src.features.media_index.tagger import SystemTagPrediction


class HistoryTestBase(MediaIndexTestBase):
    def setUp(self):
        super().setUp()
        for module_path in (
            "src.features.generation.repository",
            "src.features.generation.file_repository",
        ):
            importlib.import_module(module_path).db = self.db
        self.generation_repo = GenerationRepository()
        self.query = GenerationHistoryQuery(
            self.generation_repo, media_index_repository=self.repo
        )

    def _tagged_generation(self, gen_id, file_id, tags, ratings=None):
        gen = self.create_test_generation(gen_id, self.user_id)
        self._make_file(file_id, gen)
        self.repo.replace_file_tags(
            file_id, gen, "model-a",
            tags=[SystemTagPrediction(t, "general", c) for t, c in tags],
            ratings=ratings or {},
        )
        return gen


class TestPayloadDecoration(HistoryTestBase):
    def test_history_files_carry_system_tags_and_rating_scores(self):
        self._tagged_generation(
            "gen1", "f1", [("1girl", 0.95)],
            ratings={"general": 0.1, "sensitive": 0.1, "questionable": 0.4, "explicit": 0.4},
        )

        result = self.query.get_history(self.user_id, include_tags=False)

        files = result["generations"][0]["files"]
        assert [t["tag"] for t in files[0]["system_tags"]] == ["1girl"]
        assert files[0]["rating_scores"]["explicit"] == 0.4
        # questionable + explicit = 0.8 >= default threshold 0.6
        assert files[0]["nsfw"] is True

    def test_safe_ratings_do_not_set_nsfw(self):
        self._tagged_generation(
            "gen1", "f1", [("landscape", 0.9)],
            ratings={"general": 0.9, "sensitive": 0.05, "questionable": 0.03, "explicit": 0.02},
        )

        result = self.query.get_history(self.user_id, include_tags=False)

        assert result["generations"][0]["files"][0]["nsfw"] is False

    def test_nsfw_threshold_read_from_settings(self):
        from unittest.mock import MagicMock

        from src.features.generation.history_query import GenerationHistoryQuery

        self._tagged_generation(
            "gen1", "f1", [("tag", 0.9)],
            ratings={"general": 0.5, "sensitive": 0.2, "questionable": 0.2, "explicit": 0.1},
        )
        settings = MagicMock()
        settings.get_setting.return_value = 0.25
        query = GenerationHistoryQuery(
            self.generation_repo,
            media_index_repository=self.repo,
            settings_manager=settings,
        )

        result = query.get_history(self.user_id, include_tags=False)

        assert result["generations"][0]["files"][0]["nsfw"] is True
        settings.get_setting.assert_called_with("media_nsfw_blur_threshold", 0.6)

    def test_untagged_files_get_empty_defaults(self):
        gen = self.create_test_generation("gen1", self.user_id)
        self._make_file("f1", gen)

        result = self.query.get_history(self.user_id, include_tags=False)

        files = result["generations"][0]["files"]
        assert files[0]["system_tags"] == []
        assert files[0]["rating_scores"] is None
        assert files[0]["nsfw"] is False

    def test_detail_payload_carries_system_tags(self):
        self._tagged_generation("gen1", "f1", [("outdoors", 0.5)])

        data = self.query.get_by_id("gen1", self.user_id)

        assert [t["tag"] for t in data["files"][0]["system_tags"]] == ["outdoors"]

    def test_missing_media_index_repository_still_serializes(self):
        gen = self.create_test_generation("gen1", self.user_id)
        self._make_file("f1", gen)
        query = GenerationHistoryQuery(self.generation_repo)

        result = query.get_history(self.user_id, include_tags=False)

        assert result["generations"][0]["files"][0]["system_tags"] == []


class TestSystemTagFilter(HistoryTestBase):
    def test_filter_matches_only_generations_with_the_tag(self):
        self._tagged_generation("gen1", "f1", [("1girl", 0.9)])
        self._tagged_generation("gen2", "f2", [("landscape", 0.9)])

        result = self.query.get_history(
            self.user_id, include_tags=False, system_tag="1girl"
        )

        assert [g["id"] for g in result["generations"]] == ["gen1"]
        assert result["total"] == 1

    def test_filter_is_case_insensitive(self):
        self._tagged_generation("gen1", "f1", [("1girl", 0.9)])

        result = self.query.get_history(
            self.user_id, include_tags=False, system_tag="1GIRL"
        )

        assert result["total"] == 1

    def test_rating_rows_are_not_matchable_as_tags(self):
        self._tagged_generation(
            "gen1", "f1", [("1girl", 0.9)],
            ratings={"general": 0.9, "sensitive": 0.05, "questionable": 0.03, "explicit": 0.02},
        )

        result = self.query.get_history(
            self.user_id, include_tags=False, system_tag="general"
        )

        assert result["total"] == 0
