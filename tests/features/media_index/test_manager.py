"""MediaIndexManager: queue feeding, draining, provenance re-tagging."""

import importlib
from unittest.mock import MagicMock

import pytest

from tests.features.media_index.test_repository import MediaIndexTestBase

from src.features.media_index.manager import (
    MAX_ATTEMPTS,
    PASS_CLIP_EMBED,
    PASS_PROMPT_EMBED,
    PASS_TAGS,
    MediaIndexManager,
)
from src.features.media_index.tagger import SystemTagPrediction, TaggingResult


class FakeTagger:
    def __init__(self, provenance="model-a", result=None, error=None):
        self.provenance = provenance
        self.result = result or TaggingResult(
            tags=[SystemTagPrediction("1girl", "general", 0.9)],
            ratings={"general": 0.8, "sensitive": 0.1, "questionable": 0.05, "explicit": 0.05},
        )
        self.error = error
        self.calls = []

    def tag_image_file(self, path):
        self.calls.append(path)
        if self.error:
            raise self.error
        return self.result


class FakeFileStore:
    def __init__(self, base="/storage"):
        self.base = base

    def get_full_path(self, relative_path):
        return f"{self.base}/{relative_path}"


class FakeVisionEmbedder:
    def __init__(self, error=None):
        self.embedder_slug = "local-siglip-test"
        self.error = error
        self.image_calls = []
        self.text_calls = []

    def embed_images(self, images):
        self.image_calls.append(list(images))
        if self.error:
            raise self.error
        return [[1.0, 0.0, 0.0] for _ in images]

    def embed_texts(self, texts):
        self.text_calls.append(list(texts))
        if self.error:
            raise self.error
        return [[0.0, 1.0, 0.0] for _ in texts]


class FakeGalleryStore:
    def __init__(self, empty=True, hits=None, add_error=None):
        self.empty = empty
        self.hits = hits or []
        self.add_error = add_error
        self.added = []
        self.search_calls = []
        self.pruned = []

    def is_collection_empty(self, user_id):
        return self.empty

    def prune_stale_collections(self, user_id):
        self.pruned.append(user_id)
        return 1

    def add(self, user_id, file_id, embedding, metadata=None):
        if self.add_error:
            raise self.add_error
        self.added.append(
            {"user_id": user_id, "file_id": file_id, "embedding": embedding, "metadata": metadata}
        )
        self.empty = False

    def search(self, user_id, query_embedding, limit=100):
        self.search_calls.append({"user_id": user_id, "limit": limit})
        return list(self.hits)

    def collection_size(self, user_id):
        return len(self.hits)

    def all_generation_ids(self, user_id):
        seen = set()
        ordered = []
        for hit in self.hits:
            generation_id = hit.get("generation_id")
            if generation_id and generation_id not in seen:
                seen.add(generation_id)
                ordered.append(generation_id)
        return ordered


class FakeTextEmbedder:
    """Stands in for `src.features.prompt_database.embedding.EmbeddingProvider`
    - `embed()` is async there (it's shared with prompt-database search), so
    the fake mirrors that; `_process_prompt_embed_batch` bridges it onto the
    manager's sync call path with `asyncio.run`."""

    def __init__(self, error=None):
        self.embedder_slug = "local-bge-test"
        self.error = error
        self.calls = []

    async def embed(self, texts):
        self.calls.append(list(texts))
        if self.error:
            raise self.error
        return [[0.5, 0.5] for _ in texts]


class FakeGalleryPromptStore:
    def __init__(self, add_error=None):
        self.add_error = add_error
        self.added = []

    def add(self, user_id, file_id, embedding, prompt_text, metadata=None):
        if self.add_error:
            raise self.add_error
        self.added.append({
            "user_id": user_id, "file_id": file_id, "embedding": embedding,
            "prompt_text": prompt_text, "metadata": metadata,
        })


class ManagerTestBase(MediaIndexTestBase):
    def _manager(self, tagger=None, embedder=None, store=None, file_service=None,
                 text_embedder=None, prompt_store=None):
        self.tagger = tagger or FakeTagger()
        self.embedder = embedder or FakeVisionEmbedder()
        self.store = store or FakeGalleryStore()
        self.text_embedder = text_embedder or FakeTextEmbedder()
        self.prompt_store = prompt_store or FakeGalleryPromptStore()
        return MediaIndexManager(
            repository=self.repo,
            tagger_provider=self.tagger,
            file_service=file_service or FakeFileStore(),
            vision_embedder=self.embedder,
            gallery_vector_store=self.store,
            text_embedding_provider=self.text_embedder,
            gallery_prompt_vector_store=self.prompt_store,
        )


class TestQueueFeeding(ManagerTestBase):
    def test_completed_generation_enqueues_final_files(self):
        gen = self.create_test_generation("gen1", self.user_id)
        self._make_file("f1", gen, is_final=True)
        self._make_file("f2", gen, is_final=False)

        manager = self._manager()
        manager.on_generation_complete(gen, "completed")

        assert self._queue_row("f1")["status"] == "pending"
        assert self._queue_row("f2") is None

    def test_non_completed_states_enqueue_nothing(self):
        gen = self.create_test_generation("gen1", self.user_id)
        self._make_file("f1", gen)
        manager = self._manager()

        manager.on_generation_complete(gen, "failed")
        manager.on_generation_complete(gen, "cancelled")

        assert self._queue_row("f1") is None

    def test_enqueue_errors_never_propagate(self):
        manager = self._manager()
        manager.repository = MagicMock()
        manager.repository.enqueue_generation_files.side_effect = RuntimeError("db down")
        manager.on_generation_complete("gen1", "completed")


class TestProcessPending(ManagerTestBase):
    def test_drains_batch_writes_tags_and_marks_done(self):
        gen = self.create_test_generation("gen1", self.user_id)
        self._make_file("f1", gen)
        manager = self._manager()
        manager.on_generation_complete(gen, "completed")

        result = manager.process_pending(PASS_TAGS, batch_size=10)

        assert result == {"processed": 1, "failed": 0}
        assert self.tagger.calls == ["/storage/generations/g/f1.png"]
        data = self.repo.get_for_files(["f1"])
        assert [t["tag"] for t in data["f1"]["system_tags"]] == ["1girl"]
        assert data["f1"]["rating_scores"]["explicit"] == 0.05
        assert data["f1"]["provenance"] == "model-a"
        assert self._queue_row("f1")["status"] == "done"

    def test_batch_size_limits_processed_items(self):
        for i in range(3):
            self._make_file(f"f{i}")
        self.repo.enqueue_files(["f0", "f1", "f2"], PASS_TAGS)
        manager = self._manager()

        assert manager.process_pending(PASS_TAGS, batch_size=2)["processed"] == 2
        assert manager.process_pending(PASS_TAGS, batch_size=2)["processed"] == 1

    def test_failures_count_attempts_then_park_as_failed(self):
        self._make_file("f1")
        self.repo.enqueue_files(["f1"], PASS_TAGS)
        manager = self._manager(FakeTagger(error=RuntimeError("corrupt image")))

        for _ in range(MAX_ATTEMPTS):
            assert manager.process_pending(PASS_TAGS, batch_size=10)["failed"] == 1

        row = self._queue_row("f1")
        assert row["status"] == "failed"
        assert row["attempts"] == MAX_ATTEMPTS
        assert "corrupt image" in row["last_error"]
        assert manager.process_pending(PASS_TAGS, batch_size=10) == {"processed": 0, "failed": 0}

    def test_video_files_tag_through_their_thumbnail(self):
        self._make_file("v1", file_type="VIDEO", thumbnail="thumbs/v1.jpg")
        self.repo.enqueue_files(["v1"], PASS_TAGS)
        manager = self._manager()

        assert manager.process_pending(PASS_TAGS, batch_size=10)["processed"] == 1
        assert self.tagger.calls == ["/storage/thumbs/v1.jpg"]

    def test_video_without_thumbnail_is_skipped_but_done(self):
        self._make_file("v1", file_type="VIDEO", thumbnail=None)
        self.repo.enqueue_files(["v1"], PASS_TAGS)
        manager = self._manager()

        assert manager.process_pending(PASS_TAGS, batch_size=10)["processed"] == 1
        assert self.tagger.calls == []
        assert self._queue_row("v1")["status"] == "done"
        assert self.repo.get_for_files(["v1"]) == {}

    def test_unknown_pass_type_raises(self):
        manager = self._manager()
        with pytest.raises(ValueError, match="face_embed"):
            manager.process_pending("face_embed", batch_size=10)


class TestRetagStale(ManagerTestBase):
    def test_model_switch_deletes_old_provenance_and_requeues(self):
        self._make_file("f1")
        self.repo.enqueue_files(["f1"], PASS_TAGS)
        old_manager = self._manager(FakeTagger(provenance="model-old"))
        old_manager.process_pending(PASS_TAGS, batch_size=10)

        new_manager = self._manager(FakeTagger(provenance="model-new"))
        assert new_manager.retag_stale() == 1
        assert self.repo.get_for_files(["f1"]) == {}
        assert self._queue_row("f1")["status"] == "pending"

        new_manager.process_pending(PASS_TAGS, batch_size=10)
        assert self.repo.get_for_files(["f1"])["f1"]["provenance"] == "model-new"

    def test_retag_stale_noop_when_provenance_current(self):
        self._make_file("f1")
        self.repo.enqueue_files(["f1"], PASS_TAGS)
        manager = self._manager()
        manager.process_pending(PASS_TAGS, batch_size=10)

        assert manager.retag_stale() == 0
        assert self.repo.get_for_files(["f1"])["f1"]["provenance"] == "model-a"


class TestStatus(ManagerTestBase):
    def test_status_reports_queue_counts_and_provenance(self):
        self._make_file("f1")
        self.repo.enqueue_files(["f1"], PASS_TAGS)
        manager = self._manager()

        status = manager.status()
        assert status["queue"]["tags"]["pending"] == 1
        assert status["tagged_files"] == 0
        assert status["provenance"] == "model-a"
        assert status["gallery_embedder"] == "local-siglip-test"
        assert status["prompt_embedder"] == "local-bge-test"


class TestClipQueueFeeding(ManagerTestBase):
    def test_completed_generation_enqueues_all_passes(self):
        gen = self.create_test_generation("gen1", self.user_id)
        self._make_file("f1", gen, is_final=True)

        manager = self._manager()
        manager.on_generation_complete(gen, "completed")

        assert self._queue_row("f1", PASS_TAGS)["status"] == "pending"
        assert self._queue_row("f1", PASS_CLIP_EMBED)["status"] == "pending"
        assert self._queue_row("f1", PASS_PROMPT_EMBED)["status"] == "pending"

    def test_backfill_enqueues_all_passes_by_default(self):
        self._make_file("f1")
        manager = self._manager()

        assert manager.backfill() == 3
        assert self._queue_row("f1", PASS_TAGS) is not None
        assert self._queue_row("f1", PASS_CLIP_EMBED) is not None
        assert self._queue_row("f1", PASS_PROMPT_EMBED) is not None

    def test_backfill_can_target_one_pass(self):
        self._make_file("f1")
        manager = self._manager()

        assert manager.backfill(PASS_CLIP_EMBED) == 1
        assert self._queue_row("f1", PASS_TAGS) is None
        assert self._queue_row("f1", PASS_CLIP_EMBED) is not None


class ClipDrainTestBase(ManagerTestBase):
    def setUp(self):
        super().setUp()
        import tempfile

        self.storage_dir = tempfile.mkdtemp()

    def _write_image(self, relative_path):
        from pathlib import Path

        from PIL import Image

        target = Path(self.storage_dir) / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), (128, 64, 32)).save(target)

    def _clip_manager(self, **kwargs):
        return self._manager(file_service=FakeFileStore(base=self.storage_dir), **kwargs)


class TestClipEmbedPass(ClipDrainTestBase):
    def test_drain_embeds_batched_and_upserts_with_generation_metadata(self):
        gen = self.create_test_generation("gen1", self.user_id)
        for file_id in ("f1", "f2"):
            self._make_file(file_id, gen)
            self._write_image(f"generations/g/{file_id}.png")
        self.repo.enqueue_files(["f1", "f2"], PASS_CLIP_EMBED)
        manager = self._clip_manager()

        result = manager.process_pending(PASS_CLIP_EMBED, batch_size=10)

        assert result == {"processed": 2, "failed": 0}
        assert len(self.embedder.image_calls) == 1
        assert len(self.embedder.image_calls[0]) == 2
        assert {a["file_id"] for a in self.store.added} == {"f1", "f2"}
        upserted = self.store.added[0]
        assert upserted["user_id"] == self.user_id
        assert upserted["metadata"]["generation_id"] == gen
        assert upserted["embedding"] == [1.0, 0.0, 0.0]
        assert self._queue_row("f1", PASS_CLIP_EMBED)["status"] == "done"

    def test_unreadable_file_fails_alone(self):
        from pathlib import Path

        self._make_file("good")
        self._make_file("corrupt")
        self._write_image("generations/g/good.png")
        corrupt = Path(self.storage_dir) / "generations/g/corrupt.png"
        corrupt.parent.mkdir(parents=True, exist_ok=True)
        corrupt.write_bytes(b"not an image")
        self.repo.enqueue_files(["good", "corrupt"], PASS_CLIP_EMBED)
        manager = self._clip_manager()

        result = manager.process_pending(PASS_CLIP_EMBED, batch_size=10)

        assert result == {"processed": 1, "failed": 1}
        assert self._queue_row("good", PASS_CLIP_EMBED)["status"] == "done"
        assert self._queue_row("corrupt", PASS_CLIP_EMBED)["status"] == "pending"
        assert self._queue_row("corrupt", PASS_CLIP_EMBED)["attempts"] == 1

    def test_file_deleted_before_indexing_is_skipped_not_retried(self):
        self._make_file("good")
        self._make_file("deleted")
        self._write_image("generations/g/good.png")
        self.repo.enqueue_files(["good", "deleted"], PASS_CLIP_EMBED)
        manager = self._clip_manager()

        result = manager.process_pending(PASS_CLIP_EMBED, batch_size=10)

        # The file is gone for good, so retrying it until it exhausts its
        # attempts only produces noise.
        assert result == {"processed": 2, "failed": 0}
        assert self._queue_row("deleted", PASS_CLIP_EMBED)["status"] == "done"
        assert {a["file_id"] for a in self.store.added} == {"good"}

    def test_embed_failure_marks_batch_failed_with_attempts(self):
        self._make_file("f1")
        self._write_image("generations/g/f1.png")
        self.repo.enqueue_files(["f1"], PASS_CLIP_EMBED)
        manager = self._clip_manager(embedder=FakeVisionEmbedder(error=RuntimeError("no space")))

        for _ in range(MAX_ATTEMPTS):
            assert manager.process_pending(PASS_CLIP_EMBED, batch_size=10)["failed"] == 1

        row = self._queue_row("f1", PASS_CLIP_EMBED)
        assert row["status"] == "failed"
        assert "no space" in row["last_error"]

    def test_video_without_thumbnail_is_skipped_but_done(self):
        self._make_file("v1", file_type="VIDEO", thumbnail=None)
        self.repo.enqueue_files(["v1"], PASS_CLIP_EMBED)
        manager = self._clip_manager()

        assert manager.process_pending(PASS_CLIP_EMBED, batch_size=10)["processed"] == 1
        assert self.embedder.image_calls == []
        assert self._queue_row("v1", PASS_CLIP_EMBED)["status"] == "done"


class TestPromptEmbedPass(ManagerTestBase):
    def test_drain_embeds_batched_prompts_and_upserts_with_generation_metadata(self):
        gen = self.create_test_generation("gen1", self.user_id, form_data={"prompt": "a red fox in snow"})
        for file_id in ("f1", "f2"):
            self._make_file(file_id, gen)
        self.repo.enqueue_files(["f1", "f2"], PASS_PROMPT_EMBED)
        manager = self._manager()

        result = manager.process_pending(PASS_PROMPT_EMBED, batch_size=10)

        assert result == {"processed": 2, "failed": 0}
        assert self.text_embedder.calls == [["a red fox in snow", "a red fox in snow"]]
        assert {a["file_id"] for a in self.prompt_store.added} == {"f1", "f2"}
        upserted = self.prompt_store.added[0]
        assert upserted["user_id"] == self.user_id
        assert upserted["prompt_text"] == "a red fox in snow"
        assert upserted["metadata"]["generation_id"] == gen
        assert upserted["embedding"] == [0.5, 0.5]
        assert self._queue_row("f1", PASS_PROMPT_EMBED)["status"] == "done"

    def test_files_without_a_prompt_are_skipped_but_done(self):
        self._make_file("orphan")  # no generation link at all
        blank_gen = self.create_test_generation("gen-blank", self.user_id, form_data={"prompt": ""})
        self._make_file("blank", blank_gen)
        self.repo.enqueue_files(["orphan", "blank"], PASS_PROMPT_EMBED)
        manager = self._manager()

        result = manager.process_pending(PASS_PROMPT_EMBED, batch_size=10)

        assert result == {"processed": 2, "failed": 0}
        assert self.text_embedder.calls == []
        assert self.prompt_store.added == []
        assert self._queue_row("orphan", PASS_PROMPT_EMBED)["status"] == "done"
        assert self._queue_row("blank", PASS_PROMPT_EMBED)["status"] == "done"

    def test_embed_failure_marks_batch_failed_with_attempts(self):
        gen = self.create_test_generation("gen1", self.user_id)
        self._make_file("f1", gen)
        self.repo.enqueue_files(["f1"], PASS_PROMPT_EMBED)
        manager = self._manager(text_embedder=FakeTextEmbedder(error=RuntimeError("provider down")))

        for _ in range(MAX_ATTEMPTS):
            assert manager.process_pending(PASS_PROMPT_EMBED, batch_size=10)["failed"] == 1

        row = self._queue_row("f1", PASS_PROMPT_EMBED)
        assert row["status"] == "failed"
        assert "provider down" in row["last_error"]

    def test_store_failure_fails_only_that_item(self):
        gen = self.create_test_generation("gen1", self.user_id, form_data={"prompt": "castle at dusk"})
        for file_id in ("good", "bad"):
            self._make_file(file_id, gen)
        self.repo.enqueue_files(["good", "bad"], PASS_PROMPT_EMBED)
        real_store = self.prompt_store = FakeGalleryPromptStore()
        manager = self._manager(prompt_store=real_store)
        original_add = real_store.add

        def flaky_add(user_id, file_id, embedding, prompt_text, metadata=None):
            if file_id == "bad":
                raise RuntimeError("upsert failed")
            return original_add(user_id, file_id, embedding, prompt_text, metadata)

        real_store.add = flaky_add

        result = manager.process_pending(PASS_PROMPT_EMBED, batch_size=10)

        assert result == {"processed": 1, "failed": 1}
        assert self._queue_row("good", PASS_PROMPT_EMBED)["status"] == "done"
        assert self._queue_row("bad", PASS_PROMPT_EMBED)["status"] == "pending"


class TestGallerySearch(ManagerTestBase):
    def test_search_embeds_query_and_applies_relative_cutoff(self):
        hits = [
            {"file_id": "f1", "generation_id": "g1", "similarity": 0.30},
            {"file_id": "f2", "generation_id": "g2", "similarity": 0.25},
            {"file_id": "f3", "generation_id": "g3", "similarity": 0.15},
        ]
        manager = self._manager(store=FakeGalleryStore(empty=False, hits=hits))

        results = manager.search_gallery(self.user_id, "red fox")

        assert self.embedder.text_calls == [["red fox"]]
        assert [h["file_id"] for h in results] == ["f1", "f2"]

    def test_blank_query_returns_empty_without_embedding(self):
        manager = self._manager(store=FakeGalleryStore(empty=False))

        assert manager.search_gallery(self.user_id, "   ") == []
        assert self.embedder.text_calls == []

    def test_relative_cutoff_keeps_all_when_scores_are_close(self):
        hits = [
            {"file_id": "f1", "generation_id": "g1", "similarity": 0.12},
            {"file_id": "f2", "generation_id": "g2", "similarity": 0.05},
        ]
        assert MediaIndexManager.apply_relative_cutoff(hits) == hits

    def test_relative_cutoff_handles_empty(self):
        assert MediaIndexManager.apply_relative_cutoff([]) == []

    def test_embedder_switch_requeues_done_rows_on_search(self):
        self._make_file("f1")
        self.repo.enqueue_files(["f1"], PASS_CLIP_EMBED)
        item = self.repo.claim_batch(PASS_CLIP_EMBED, 10, MAX_ATTEMPTS)[0]
        self.repo.mark_done(item.id)
        manager = self._manager(store=FakeGalleryStore(empty=True))

        manager.search_gallery(self.user_id, "castle")

        row = self._queue_row("f1", PASS_CLIP_EMBED)
        assert row["status"] == "pending"
        assert row["attempts"] == 0

    def test_gallery_collection_size_delegates_to_the_vector_store(self):
        hits = [
            {"file_id": "f1", "generation_id": "g1", "similarity": 0.3},
            {"file_id": "f2", "generation_id": "g2", "similarity": 0.2},
        ]
        manager = self._manager(store=FakeGalleryStore(empty=False, hits=hits))

        assert manager.gallery_collection_size(self.user_id) == 2

    def test_all_gallery_generation_ids_delegates_to_the_vector_store(self):
        hits = [
            {"file_id": "f1", "generation_id": "g1", "similarity": 0.3},
            {"file_id": "f2", "generation_id": "g2", "similarity": 0.2},
        ]
        manager = self._manager(store=FakeGalleryStore(empty=False, hits=hits))

        assert manager.all_gallery_generation_ids(self.user_id) == ["g1", "g2"]

    def test_populated_collection_is_not_requeued(self):
        self._make_file("f1")
        self.repo.enqueue_files(["f1"], PASS_CLIP_EMBED)
        item = self.repo.claim_batch(PASS_CLIP_EMBED, 10, MAX_ATTEMPTS)[0]
        self.repo.mark_done(item.id)
        manager = self._manager(store=FakeGalleryStore(empty=False))

        manager.search_gallery(self.user_id, "castle")

        assert self._queue_row("f1", PASS_CLIP_EMBED)["status"] == "done"


class TestGalleryRebuildSettlement(ClipDrainTestBase):
    """The stale (pre-switch) gallery collection is only dropped once a
    healing rebuild has fully - and successfully - drained."""

    def _switch_embedder_and_heal(self, manager):
        """f1 is 'done' under the old collection; healing (the same detection
        `search_gallery` runs before it ever touches the embedder) puts it
        back into the queue. Called directly rather than through
        `search_gallery` so a test can pair it with an embedder that fails
        AFTER healing, during the `process_pending` drain."""
        self._make_file("f1")
        self._write_image("generations/g/f1.png")
        self.repo.enqueue_files(["f1"], PASS_CLIP_EMBED)
        item = self.repo.claim_batch(PASS_CLIP_EMBED, 10, MAX_ATTEMPTS)[0]
        self.repo.mark_done(item.id)
        manager._heal_stale_gallery_collection(self.user_id)
        assert self._queue_row("f1", PASS_CLIP_EMBED)["status"] == "pending"
        assert self.user_id in manager._gallery_rebuild_pending

    def test_stale_collection_pruned_once_rebuild_drains_successfully(self):
        manager = self._clip_manager()
        self._switch_embedder_and_heal(manager)

        result = manager.process_pending(PASS_CLIP_EMBED, batch_size=10)

        assert result == {"processed": 1, "failed": 0}
        assert self.store.pruned == [self.user_id]
        assert self.user_id not in manager._gallery_rebuild_pending

    def test_stale_collection_left_intact_while_rebuild_still_has_pending_work(self):
        gen = self.create_test_generation("gen2", self.user_id)
        self._make_file("f2", gen, is_final=True)
        self._write_image("generations/g/f2.png")
        manager = self._clip_manager()
        self._switch_embedder_and_heal(manager)
        # A second, unrelated file queues normally alongside the healing
        # requeue - the rebuild isn't "done" until every clip_embed row for
        # this user drains, not just the ones healing explicitly requeued.
        self.repo.enqueue_files(["f2"], PASS_CLIP_EMBED)

        result = manager.process_pending(PASS_CLIP_EMBED, batch_size=1)

        assert result["processed"] == 1
        assert self.store.pruned == []
        assert self.user_id in manager._gallery_rebuild_pending

        manager.process_pending(PASS_CLIP_EMBED, batch_size=1)
        assert self.store.pruned == [self.user_id]

    def test_stale_collection_never_pruned_after_a_permanent_failure(self):
        manager = self._clip_manager(embedder=FakeVisionEmbedder(error=RuntimeError("no space")))
        self._switch_embedder_and_heal(manager)

        for _ in range(MAX_ATTEMPTS):
            manager.process_pending(PASS_CLIP_EMBED, batch_size=10)

        assert self._queue_row("f1", PASS_CLIP_EMBED)["status"] == "failed"
        assert self.store.pruned == []
        # Left in the pending set forever (until an admin retries the failed
        # row) rather than settled - a stuck failure must never look done.
        assert self.user_id in manager._gallery_rebuild_pending

    def test_users_never_seen_mid_rebuild_are_never_pruned(self):
        """A user with `done` rows under a stale collection who never
        searched (so `_heal_stale_gallery_collection` never ran) must not
        have their old collection dropped just because their queue happens
        to be empty - pruning is gated on having actually observed a
        rebuild start, not on an incidental empty queue."""
        self._make_file("f1")
        self._write_image("generations/g/f1.png")
        self.repo.enqueue_files(["f1"], PASS_CLIP_EMBED)
        manager = self._clip_manager()

        manager.process_pending(PASS_CLIP_EMBED, batch_size=10)

        assert self.store.pruned == []
