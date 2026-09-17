"""MediaIndexRepository: queue lifecycle and system-tag storage."""

import importlib

from tests.fixtures.persistence_base import PersistenceTestBase

from src.features.media_index.repository import MediaIndexRepository
from src.features.media_index.tagger import SystemTagPrediction

_DB_BOUND_MODULES = (
    "src.features.media_index.repository",
    "src.platform.util.ids",
)


class MediaIndexTestBase(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        importlib.import_module("src.features.media_index.repository").db = self.db
        self.repo = MediaIndexRepository()
        self.user_id = self.create_test_user()

    def _make_file(self, file_id, generation_id=None, is_final=True,
                   file_type="IMAGE", thumbnail=None):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO files (id, file_path, file_type, user_id, is_final, thumbnail_medium)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (file_id, f"generations/g/{file_id}.png", file_type, self.user_id,
                 is_final, thumbnail),
            )
            if generation_id:
                cursor.execute(
                    """
                    INSERT INTO generation_files (id, generation_id, file_id)
                    VALUES (?, ?, ?)
                    """,
                    (f"gf-{file_id}", generation_id, file_id),
                )
        return file_id

    def _queue_row(self, file_id, pass_type="tags"):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM media_index_queue WHERE file_id = ? AND pass_type = ?",
                (file_id, pass_type),
            )
            return cursor.fetchone()


class TestQueue(MediaIndexTestBase):
    def test_enqueue_is_deduplicated_per_file_and_pass(self):
        self._make_file("f1")
        assert self.repo.enqueue_files(["f1"], "tags") == 1
        assert self.repo.enqueue_files(["f1"], "tags") == 0
        assert self.repo.enqueue_files(["f1"], "clip_embed") == 1

    def test_requeue_resets_done_and_failed_rows(self):
        self._make_file("f1")
        self.repo.enqueue_files(["f1"], "tags")
        item = self.repo.claim_batch("tags", 10, 3)[0]
        self.repo.mark_done(item.id)

        assert self.repo.enqueue_files(["f1"], "tags") == 0
        assert self.repo.enqueue_files(["f1"], "tags", requeue=True) == 1
        row = self._queue_row("f1")
        assert row["status"] == "pending"
        assert row["attempts"] == 0

    def test_enqueue_generation_files_takes_only_final_files(self):
        gen = self.create_test_generation("gen1", self.user_id)
        self._make_file("final", gen, is_final=True)
        self._make_file("preview", gen, is_final=False)

        assert self.repo.enqueue_generation_files(gen, "tags") == 1
        assert self._queue_row("final") is not None
        assert self._queue_row("preview") is None

    def test_backfill_skips_files_already_queued(self):
        self._make_file("f1")
        self._make_file("f2")
        self.repo.enqueue_files(["f1"], "tags")

        assert self.repo.enqueue_backfill("tags") == 1
        assert self._queue_row("f2") is not None

    def test_claim_batch_marks_processing_and_joins_file_context(self):
        gen = self.create_test_generation("gen1", self.user_id)
        self._make_file("f1", gen)
        self.repo.enqueue_files(["f1"], "tags")

        items = self.repo.claim_batch("tags", 10, 3)
        assert len(items) == 1
        item = items[0]
        assert item.file_id == "f1"
        assert item.generation_id == gen
        assert item.file_type == "IMAGE"
        assert item.file_path == "generations/g/f1.png"
        assert item.prompt_text == "test prompt"
        assert self._queue_row("f1")["status"] == "processing"
        assert self.repo.claim_batch("tags", 10, 3) == []

    def test_claim_batch_prompt_text_is_none_without_a_generation(self):
        self._make_file("f1")
        self.repo.enqueue_files(["f1"], "prompt_embed")

        item = self.repo.claim_batch("prompt_embed", 10, 3)[0]

        assert item.generation_id is None
        assert item.prompt_text is None

    def test_claim_batch_prompt_text_is_none_when_form_data_has_no_prompt(self):
        gen = self.create_test_generation("gen1", self.user_id, form_data={"steps": 20})
        self._make_file("f1", gen)
        self.repo.enqueue_files(["f1"], "prompt_embed")

        item = self.repo.claim_batch("prompt_embed", 10, 3)[0]

        assert item.prompt_text is None

    def test_mark_failed_retries_until_attempts_exhaust(self):
        self._make_file("f1")
        self.repo.enqueue_files(["f1"], "tags")

        for attempt in range(1, 3):
            item = self.repo.claim_batch("tags", 10, 3)[0]
            self.repo.mark_failed(item.id, "boom", 3)
            row = self._queue_row("f1")
            assert row["attempts"] == attempt
            assert row["status"] == "pending"

        item = self.repo.claim_batch("tags", 10, 3)[0]
        self.repo.mark_failed(item.id, "boom", 3)
        row = self._queue_row("f1")
        assert row["status"] == "failed"
        assert row["last_error"] == "boom"
        assert self.repo.claim_batch("tags", 10, 3) == []

    def test_queue_counts_grouped_by_pass_and_status(self):
        self._make_file("f1")
        self._make_file("f2")
        self.repo.enqueue_files(["f1", "f2"], "tags")
        item = self.repo.claim_batch("tags", 1, 3)[0]
        self.repo.mark_done(item.id)

        counts = self.repo.queue_counts()
        assert counts["tags"]["done"] == 1
        assert counts["tags"]["pending"] == 1


class TestSystemTags(MediaIndexTestBase):
    def _tag(self, name, category="general", confidence=0.9):
        return SystemTagPrediction(tag=name, category=category, confidence=confidence)

    def test_replace_and_fetch_splits_ratings_from_tags(self):
        gen = self.create_test_generation("gen1", self.user_id)
        self._make_file("f1", gen)

        self.repo.replace_file_tags(
            "f1", gen, "model-a",
            tags=[self._tag("1girl", confidence=0.99), self._tag("miku", "character", 0.8)],
            ratings={"general": 0.7, "sensitive": 0.2, "questionable": 0.05, "explicit": 0.01},
        )

        data = self.repo.get_for_files(["f1"])
        assert data["f1"]["provenance"] == "model-a"
        assert [t["tag"] for t in data["f1"]["system_tags"]] == ["1girl", "miku"]
        assert data["f1"]["rating_scores"]["explicit"] == 0.01
        assert "general" not in [t["tag"] for t in data["f1"]["system_tags"]]

    def test_replace_overwrites_previous_tags(self):
        self._make_file("f1")
        self.repo.replace_file_tags("f1", None, "model-a", [self._tag("old")], {})
        self.repo.replace_file_tags("f1", None, "model-b", [self._tag("new")], {})

        data = self.repo.get_for_files(["f1"])
        assert [t["tag"] for t in data["f1"]["system_tags"]] == ["new"]
        assert data["f1"]["provenance"] == "model-b"

    def test_stale_provenance_detection_and_deletion(self):
        self._make_file("f1")
        self._make_file("f2")
        self.repo.replace_file_tags("f1", None, "model-old", [self._tag("a")], {})
        self.repo.replace_file_tags("f2", None, "model-new", [self._tag("b")], {})

        assert self.repo.stale_file_ids("model-new") == ["f1"]
        assert self.repo.delete_not_provenance("model-new") == 1
        assert self.repo.get_for_files(["f1"]) == {}
        assert "f2" in self.repo.get_for_files(["f2"])

    def test_deleting_file_cascades_tags_and_queue_rows(self):
        self._make_file("f1")
        self.repo.enqueue_files(["f1"], "tags")
        self.repo.replace_file_tags("f1", None, "model-a", [self._tag("a")], {})

        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM files WHERE id = 'f1'")

        assert self.repo.get_for_files(["f1"]) == {}
        assert self._queue_row("f1") is None


class TestConcurrentClaims(MediaIndexTestBase):
    def _enqueue(self, count):
        ids = [self._make_file(f"c{i}") for i in range(count)]
        self.repo.enqueue_files(ids, "tags")
        return ids

    def test_overlapping_claimers_never_receive_the_same_item(self):
        import threading

        rounds = 15
        claimers = 4
        per_claim = 3
        for round_index in range(rounds):
            with self.db.get_cursor() as cursor:
                cursor.execute("DELETE FROM media_index_queue")
                cursor.execute("DELETE FROM files WHERE id LIKE 'c%'")
            self._enqueue(claimers * per_claim)

            barrier = threading.Barrier(claimers)
            claimed = []
            errors = []
            guard = threading.Lock()

            def claim():
                try:
                    barrier.wait(timeout=10)
                    items = MediaIndexRepository().claim_batch("tags", per_claim, 3)
                    with guard:
                        claimed.extend(item.id for item in items)
                except Exception as exc:
                    with guard:
                        errors.append(exc)

            threads = [threading.Thread(target=claim) for _ in range(claimers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

            assert not errors, f"round {round_index}: {errors}"
            assert len(claimed) == len(set(claimed)), f"round {round_index}: item claimed twice"
            assert len(claimed) == claimers * per_claim

    def test_claims_come_oldest_first_and_respect_the_batch_size(self):
        with self.db.get_cursor() as cursor:
            for index, created in enumerate(("2026-01-03", "2026-01-01", "2026-01-02")):
                file_id = f"o{index}"
                cursor.execute(
                    "INSERT INTO files (id, file_path, file_type, user_id, is_final) VALUES (?, ?, 'IMAGE', ?, 1)",
                    (file_id, f"generations/g/{file_id}.png", self.user_id),
                )
                cursor.execute(
                    "INSERT INTO media_index_queue (id, file_id, pass_type, status, attempts, created_at, updated_at) "
                    "VALUES (?, ?, 'tags', 'pending', 0, ?, ?)",
                    (f"q{index}", file_id, created, created),
                )

        first = self.repo.claim_batch("tags", 2, 3)
        second = self.repo.claim_batch("tags", 2, 3)

        assert [item.file_id for item in first] == ["o1", "o2"]
        assert [item.file_id for item in second] == ["o0"]

    def test_items_past_the_retry_limit_are_not_claimed(self):
        self._make_file("r1")
        self.repo.enqueue_files(["r1"], "tags")
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE media_index_queue SET attempts = 3 WHERE file_id = 'r1'")

        assert self.repo.claim_batch("tags", 10, 3) == []
        assert self._queue_row("r1")["status"] == "pending"
