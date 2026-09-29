from src.features.content_safety.ledger_repository import ContentLedger
from tests.features.content_safety.fakes import FakeSettings
from tests.features.media_index.test_repository import MediaIndexTestBase


class TestLedger(MediaIndexTestBase):
    def setUp(self):
        super().setUp()
        self.settings = FakeSettings()
        self.ledger = ContentLedger(self.settings)

    def test_state_is_derived_from_the_score_and_the_current_threshold(self):
        self.ledger.record_score("a.png", 0.5)

        assert self.ledger.states(["a.png"]) == {"a.png": "safe"}

        self.settings.values["media_nsfw_blur_threshold"] = 0.3

        assert self.ledger.states(["a.png"]) == {"a.png": "flagged"}

    def test_missing_and_explicitly_unrated_paths_are_unrated(self):
        self.ledger.record_unrated("b.png")

        assert self.ledger.states(["b.png", "never-seen.png"]) == {
            "b.png": "unrated", "never-seen.png": "unrated",
        }

    def test_rerating_replaces_the_score(self):
        self.ledger.record_score("a.png", 0.9)
        self.ledger.record_score("a.png", 0.1)

        assert self.ledger.scores(["a.png"])["a.png"] == 0.1

    def test_a_tagger_refresh_never_overwrites_the_gate_verdict(self):
        self.ledger.record_score("v.mp4", 0.95, source="gate")

        self.ledger.record_tagger_score("v.mp4", 0.05, "wd")

        assert self.ledger.scores(["v.mp4"])["v.mp4"] == 0.95

    def test_a_tagger_score_fills_an_unrated_row_and_updates_its_own_rows(self):
        self.ledger.record_unrated("c.png")
        self.ledger.record_tagger_score("c.png", 0.2, "wd")
        self.ledger.record_tagger_score("c.png", 0.3, "wd")

        assert self.ledger.scores(["c.png"])["c.png"] == 0.3

    def test_generation_states_follow_the_generations_files(self):
        gen = self.create_test_generation("gen1", self.user_id)
        self._make_file("f1", gen)
        self._make_file("f2", gen)
        self.ledger.record_score("generations/g/f1.png", 0.1)

        assert self.ledger.generation_states(["gen1", "other"]) == {"gen1": ["safe", "unrated"]}

    def test_backfill_progress_counts_rated_files(self):
        self._make_file("f1")
        self._make_file("f2")
        self.ledger.record_score("generations/g/f1.png", 0.1)

        assert self.ledger.backfill_progress() == {"total": 2, "rated": 1}

    def test_unrated_files_pages_by_id_and_skips_rated_ones(self):
        for name in ("f1", "f2", "f3"):
            self._make_file(name)
        self.ledger.record_score("generations/g/f2.png", 0.1)

        first = self.ledger.unrated_files(1)
        rest = self.ledger.unrated_files(10, after=first[0]["id"])

        assert [row["id"] for row in first] == ["f1"]
        assert [row["id"] for row in rest] == ["f3"]

    def test_events_are_recorded_without_a_matched_word(self):
        self.ledger.log_event("banned_prompt", user_id=self.user_id, detail={"entry": 3})

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT kind, detail_json FROM content_safety_events")
            row = cursor.fetchone()
        assert row["kind"] == "banned_prompt"
        assert row["detail_json"] == '{"entry": 3}'
