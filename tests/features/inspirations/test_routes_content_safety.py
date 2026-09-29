import dataclasses

from src.features.content_safety.gate import ContentGate
from src.features.content_safety.ledger_repository import ContentLedger
from src.features.content_safety.manager import ContentSafetyManager
from tests.features.content_safety.fakes import FakeResolver, FakeSettings, FakeTagger
from tests.features.inspirations.test_routes import InspirationRoutesTestBase


class TestRestrictedFeed(InspirationRoutesTestBase):

    def setUp(self):
        super().setUp()
        settings = FakeSettings()
        self.ledger = ContentLedger(settings)
        self.restricted = True
        self.manager = ContentSafetyManager(
            settings=settings,
            resolver=FakeResolver("blocked", restricted=True),
            ledger=self.ledger,
            gate=ContentGate(FakeTagger(), settings),
        )
        self.controller.collaborators = dataclasses.replace(self.collaborators, content_safety=self.manager)

    def _published(self, title, score):
        insp, source = self._publish(title=title)
        generation_files = self.ledger.generation_states([insp.source_generation_id])
        assert generation_files == {insp.source_generation_id: ["unrated"]}
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT f.file_path FROM files f JOIN generation_files gf ON gf.file_id = f.id "
                "WHERE gf.generation_id = ?",
                (insp.source_generation_id,),
            )
            path = cursor.fetchone()["file_path"]
        if score is not None:
            self.ledger.record_score(path, score)
        return insp

    def test_only_inspirations_from_fully_safe_generations_reach_a_restricted_viewer(self):
        safe = self._published("Safe", 0.05)
        self._published("Flagged", 0.95)
        self._published("Unrated", None)

        feed = self.client.get("/api/inspirations").json()["data"]

        assert [item["id"] for item in feed["items"]] == [safe.id]
        assert feed["total"] == 1

    def test_a_flagged_inspiration_detail_and_params_are_not_found(self):
        flagged = self._published("Flagged", 0.95)

        assert self.client.get(f"/api/inspirations/{flagged.id}").status_code == 404
        assert self.client.get(f"/api/inspirations/{flagged.id}/params").status_code == 404

    def test_a_safe_inspiration_detail_is_served(self):
        safe = self._published("Safe", 0.05)

        assert self.client.get(f"/api/inspirations/{safe.id}").status_code == 200

    def test_an_unrestricted_viewer_sees_everything(self):
        self.manager.resolver = FakeResolver("blocked", restricted=False)
        self._published("Flagged", 0.95)

        feed = self.client.get("/api/inspirations").json()["data"]

        assert feed["total"] == 1
        assert len(feed["items"]) == 1
