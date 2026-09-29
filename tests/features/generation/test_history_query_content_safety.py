import pytest

from src.features.content_safety.ledger_repository import ContentLedger
from src.features.content_safety.manager import ContentSafetyManager
from tests.features.content_safety.fakes import FakeResolver, FakeSettings, FakeTagger
from tests.features.media_index.test_history_system_tags import HistoryTestBase

from src.features.content_safety.gate import ContentGate
from src.features.generation.exceptions import GenerationNotFoundException
from src.features.generation.history_query import GenerationHistoryQuery


class ContentHistoryBase(HistoryTestBase):
    def build_query(self, mode="blocked", restricted=True):
        settings = FakeSettings()
        self.ledger = ContentLedger(settings)
        manager = ContentSafetyManager(
            settings=settings,
            resolver=FakeResolver(mode, restricted),
            ledger=self.ledger,
            gate=ContentGate(FakeTagger(), settings),
        )
        return GenerationHistoryQuery(
            self.generation_repo, media_index_repository=self.repo, content_safety=manager
        )

    def generation_with(self, gen_id, file_id, score=None):
        gen = self.create_test_generation(gen_id, self.user_id)
        self._make_file(file_id, gen)
        if score is not None:
            self.ledger.record_score(f"generations/g/{file_id}.png", score)
        return gen

    def file_ids(self, query):
        result = query.get_history(self.user_id, include_tags=False)
        return {
            gen["id"]: [f["id"] for f in gen["files"]] for gen in result["generations"]
        }


class TestRestrictedViewer(ContentHistoryBase):
    def test_only_safe_files_reach_a_restricted_viewer(self):
        query = self.build_query()
        self.generation_with("safe-gen", "safe-file", 0.05)
        self.generation_with("flagged-gen", "flagged-file", 0.95)

        assert self.file_ids(query) == {"safe-gen": ["safe-file"]}

    def test_unrated_files_become_url_free_placeholders(self):
        query = self.build_query()
        self.generation_with("new-gen", "new-file")

        result = query.get_history(self.user_id, include_tags=False)

        placeholder = result["generations"][0]["files"][0]
        assert placeholder["content_state"] == "unrated"
        assert placeholder["id"] == "new-file"
        assert "file_path" not in placeholder
        assert not any(key.startswith("thumbnail") for key in placeholder)

    def test_a_generation_with_only_flagged_files_is_hidden(self):
        query = self.build_query()
        self.generation_with("bad-gen", "bad-file", 0.99)

        assert self.file_ids(query) == {}

    def test_detail_of_a_fully_flagged_generation_is_not_found(self):
        query = self.build_query()
        self.generation_with("bad-gen", "bad-file", 0.99)

        with pytest.raises(GenerationNotFoundException):
            query.get_by_id("bad-gen", self.user_id)

    def test_detail_of_a_safe_generation_is_returned(self):
        query = self.build_query()
        self.generation_with("ok-gen", "ok-file", 0.01)

        detail = query.get_by_id("ok-gen", self.user_id)

        assert [f["id"] for f in detail["files"]] == ["ok-file"]

    def test_threshold_change_reclassifies_from_the_stored_score(self):
        query = self.build_query()
        self.generation_with("mid-gen", "mid-file", 0.5)
        assert self.file_ids(query) == {"mid-gen": ["mid-file"]}

        self.ledger.settings.values["media_nsfw_blur_threshold"] = 0.4

        assert self.file_ids(query) == {}


class TestOpenViewer(ContentHistoryBase):
    def test_an_unrestricted_viewer_keeps_every_file_and_sees_the_flag(self):
        query = self.build_query(mode="blur", restricted=False)
        self.generation_with("bad-gen", "bad-file", 0.95)
        self.generation_with("new-gen", "new-file")

        result = query.get_history(self.user_id, include_tags=False)

        files = {f["id"]: f for gen in result["generations"] for f in gen["files"]}
        assert files["bad-file"]["nsfw"] is True
        assert files["bad-file"]["content_flagged"] is True
        assert files["new-file"]["content_flagged"] is False
        assert "content_state" not in files["new-file"]

    def test_allowed_policy_marks_nsfw_but_does_not_force_the_blur(self):
        query = self.build_query(mode="allowed", restricted=False)
        self.generation_with("bad-gen", "bad-file", 0.95)

        file_dict = query.get_history(self.user_id, include_tags=False)["generations"][0]["files"][0]

        assert file_dict["nsfw"] is True
        assert file_dict["content_flagged"] is False

    def test_without_a_content_safety_manager_files_only_gain_the_flag_field(self):
        query = GenerationHistoryQuery(self.generation_repo, media_index_repository=self.repo)
        gen = self.create_test_generation("plain-gen", self.user_id)
        self._make_file("plain-file", gen)

        file_dict = query.get_history(self.user_id, include_tags=False)["generations"][0]["files"][0]

        assert file_dict["content_flagged"] is False


class TestViewerIsRequired(ContentHistoryBase):
    def test_serialize_generations_needs_an_explicit_viewer(self):
        query = self.build_query()

        with pytest.raises(TypeError):
            query.serialize_generations([], False)

    def test_serialize_generations_filters_for_the_named_viewer(self):
        query = self.build_query()
        self.generation_with("bad-gen", "bad-file", 0.99)
        generations = self.generation_repo.get_all(user_id=self.user_id, include_files=True)

        assert query.serialize_generations(generations, False, viewer_id=self.user_id) == []

    def test_is_viewable_only_restricts_restricted_viewers(self):
        query = self.build_query()
        self.generation_with("bad-gen", "bad-file", 0.99)
        self.generation_with("ok-gen", "ok-file", 0.01)

        assert query.is_viewable("ok-gen", self.user_id) is True
        assert query.is_viewable("bad-gen", self.user_id) is False
        assert self.build_query(mode="blur", restricted=False).is_viewable("bad-gen", self.user_id) is True
