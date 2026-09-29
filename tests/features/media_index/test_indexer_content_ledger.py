from types import SimpleNamespace

import pytest

from src.features.content_safety.ledger_repository import ContentLedger
from src.features.media_index.indexer import PASS_TAGS
from tests.features.content_safety.fakes import FakeSettings
from tests.features.media_index.test_indexer import IndexerTestBase


class TestTaggerFeedsTheLedger(IndexerTestBase):
    def _drain(self, file_id):
        gen = self.create_test_generation("gen1", self.user_id)
        self._make_file(file_id, gen)
        indexer = self._indexer()
        self.ledger = ContentLedger(FakeSettings())
        indexer.content_safety = SimpleNamespace(ledger=self.ledger)
        indexer.on_generation_complete(gen, "completed")
        indexer.process_pending(PASS_TAGS, batch_size=5)
        return indexer

    def test_the_rated_score_lands_in_the_ledger_under_the_files_path(self):
        self._drain("f1")

        assert self.ledger.scores(["generations/g/f1.png"])["generations/g/f1.png"] == pytest.approx(0.1)

    def test_without_a_ledger_tagging_is_unchanged(self):
        gen = self.create_test_generation("gen1", self.user_id)
        self._make_file("f1", gen)
        indexer = self._indexer()
        indexer.on_generation_complete(gen, "completed")

        result = indexer.process_pending(PASS_TAGS, batch_size=5)

        assert result == {"processed": 1, "failed": 0}


class TestSemanticSearchIsFilteredForRestrictedViewers(IndexerTestBase):
    def _search(self, restricted):
        gen = self.create_test_generation("gen1", self.user_id)
        self._make_file("f1", gen)
        self._make_file("f2", gen)
        hits = [
            {"file_id": "f1", "generation_id": gen, "similarity": 0.9},
            {"file_id": "f2", "generation_id": gen, "similarity": 0.88},
        ]
        indexer = self._indexer(store=None)
        indexer.gallery_vector_store.hits = hits
        indexer.content_safety = SimpleNamespace(
            is_restricted=lambda user_id: restricted,
            filter_paths=lambda user_id, paths: {"generations/g/f1.png"} & set(paths),
        )
        return [hit["file_id"] for hit in indexer.search_gallery_embedding(self.user_id, [1.0, 0.0, 0.0])]

    def test_a_restricted_viewer_only_gets_hits_on_safe_files(self):
        assert self._search(restricted=True) == ["f1"]

    def test_an_unrestricted_viewer_gets_every_hit(self):
        assert self._search(restricted=False) == ["f1", "f2"]
