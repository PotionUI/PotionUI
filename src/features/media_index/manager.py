"""Media index manager: feeds and drains the reusable index queue.

``process_pending(pass_type, batch_size)`` is the seam external callers
(admin endpoint today, an automation node later) drain the queue through.
Passes register a processor for their ``pass_type``: per-item (``tags``) or
whole-batch (``clip_embed``, ``prompt_embed`` - so a batch embeds in one
model forward); the queue schema and drain loop are shared.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from src.features.media_index.records import MediaIndexQueueItem
from src.features.media_index.repository import MediaIndexRepository
from src.features.media_index.tagger import WDTaggerProvider

if TYPE_CHECKING:
    from src.platform.filesystem import FileStore
    from src.features.media_index.gallery_vector_store import GalleryVectorStore
    from src.features.media_index.gallery_prompt_vector_store import GalleryPromptVectorStore
    from src.features.media_index.vision_embedder import SiglipVisionEmbedder
    from src.features.prompt_database.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)

PASS_TAGS = "tags"
PASS_CLIP_EMBED = "clip_embed"
PASS_PROMPT_EMBED = "prompt_embed"

MAX_ATTEMPTS = 3

SEMANTIC_TOP_K = 100
RELATIVE_CUTOFF = 0.1


class MediaIndexManager:
    def __init__(
        self,
        repository: MediaIndexRepository,
        tagger_provider: WDTaggerProvider,
        file_service: "FileStore",
        vision_embedder: "SiglipVisionEmbedder",
        gallery_vector_store: "GalleryVectorStore",
        text_embedding_provider: "EmbeddingProvider",
        gallery_prompt_vector_store: "GalleryPromptVectorStore",
    ):
        self.repository = repository
        self.tagger_provider = tagger_provider
        self.file_service = file_service
        self.vision_embedder = vision_embedder
        self.gallery_vector_store = gallery_vector_store
        # The same text embedder that backs prompt-database search
        # (`src.features.prompt_database.embedding`), reused here rather than
        # a second one - `PASS_PROMPT_EMBED` embeds a file's generation
        # prompt, not its image content, so it needs a text encoder, not the
        # vision one above.
        self.text_embedding_provider = text_embedding_provider
        self.gallery_prompt_vector_store = gallery_prompt_vector_store
        # Users whose gallery collection a vision-model switch has requeued
        # (see `_heal_stale_gallery_collection`) - process-lifetime, in-memory
        # only. Drives `_settle_gallery_rebuilds`: the stale collection is
        # never dropped for a user who isn't in this set, even if their queue
        # happens to be empty (an app restart mid-rebuild loses this and just
        # leaves the stale collection in place rather than risking an early
        # drop - the queue rows themselves still drain normally either way).
        self._gallery_rebuild_pending: set = set()
        self._processors: Dict[str, Callable[[MediaIndexQueueItem], None]] = {
            PASS_TAGS: self._process_tags_item,
        }
        self._batch_processors: Dict[
            str, Callable[[List[MediaIndexQueueItem]], Dict[str, Optional[str]]]
        ] = {
            PASS_CLIP_EMBED: self._process_clip_batch,
            PASS_PROMPT_EMBED: self._process_prompt_embed_batch,
        }

    @property
    def pass_types(self) -> Tuple[str, ...]:
        return tuple(self._processors) + tuple(self._batch_processors)

    # --- Queue feeding ---------------------------------------------------------

    def on_generation_complete(self, generation_id: str, status: str) -> None:
        """Best-effort enqueue at the generation-completion seam."""
        if status != "completed":
            return
        for pass_type in self.pass_types:
            try:
                count = self.repository.enqueue_generation_files(generation_id, pass_type)
                if count:
                    logger.debug(
                        "media_index: queued %d file(s) of generation %s for %s",
                        count, generation_id, pass_type,
                    )
            except Exception:
                logger.exception(
                    "media_index: %s enqueue failed for generation %s",
                    pass_type, generation_id,
                )

    def backfill(self, pass_type: Optional[str] = None) -> int:
        passes = (pass_type,) if pass_type else self.pass_types
        return sum(self.repository.enqueue_backfill(p) for p in passes)

    def retag_stale(self) -> int:
        """Drop tags written by a previous model and requeue those files."""
        current = self.tagger_provider.provenance
        stale_files = self.repository.stale_file_ids(current)
        if not stale_files:
            return 0
        self.repository.delete_not_provenance(current)
        self.repository.enqueue_files(stale_files, PASS_TAGS, requeue=True)
        return len(stale_files)

    def _heal_stale_gallery_collection(self, user_id: str) -> int:
        """Requeue a user's embedded files after a vision-model switch.

        The gallery collection is namespaced by embedder slug, so a model
        switch lands searches in a fresh, empty collection while the queue
        still holds ``done`` rows written against the old one. Detect that
        (empty collection + done rows) and flip those rows back to pending;
        the next drain re-embeds into the active collection.
        """
        if not self.gallery_vector_store.is_collection_empty(user_id):
            return 0
        done_files = self.repository.done_file_ids_for_user(user_id, PASS_CLIP_EMBED)
        if not done_files:
            return 0
        self.repository.enqueue_files(done_files, PASS_CLIP_EMBED, requeue=True)
        self._gallery_rebuild_pending.add(user_id)
        logger.info(
            "media_index: requeued %d file(s) of user %s for %s (embedder switch)",
            len(done_files), user_id, PASS_CLIP_EMBED,
        )
        return len(done_files)

    def _settle_gallery_rebuilds(self, user_ids: "set[str]") -> None:
        """Drop a user's superseded gallery collection once its post-switch
        rebuild has fully drained - never before (a failure mid-rebuild must
        leave the old collection intact, per `has_unfinished_queue_rows`).

        Only checks users this manager itself saw enter a rebuild via
        `_heal_stale_gallery_collection` (`_gallery_rebuild_pending`) -
        pruning is an optimization, not a correctness requirement, so a user
        never observed to be mid-rebuild is left alone rather than guessed at.
        """
        for user_id in user_ids & self._gallery_rebuild_pending:
            if self.repository.has_unfinished_queue_rows(user_id, PASS_CLIP_EMBED):
                continue
            self._gallery_rebuild_pending.discard(user_id)
            dropped = self.gallery_vector_store.prune_stale_collections(user_id)
            if dropped:
                logger.info(
                    "media_index: dropped %d stale gallery collection(s) for user %s "
                    "after embedder-switch rebuild completed",
                    dropped, user_id,
                )

    # --- Queue draining --------------------------------------------------------

    def process_pending(self, pass_type: str = PASS_TAGS, batch_size: int = 8) -> Dict[str, int]:
        """Drain up to ``batch_size`` queue items of ``pass_type``.

        Returns ``{"processed": n, "failed": n}``. Synchronous and CPU-bound;
        callers on the event loop should wrap it in a thread.
        """
        processor = self._processors.get(pass_type)
        batch_processor = self._batch_processors.get(pass_type)
        if processor is None and batch_processor is None:
            raise ValueError(f"Unknown media index pass type: {pass_type}")

        items = self.repository.claim_batch(pass_type, batch_size, MAX_ATTEMPTS)
        processed = 0
        failed = 0

        if batch_processor is not None:
            outcomes = batch_processor(items)
            for item in items:
                error = outcomes.get(item.id)
                if error is None:
                    self.repository.mark_done(item.id)
                    processed += 1
                else:
                    logger.error(
                        "media_index: %s pass failed for file %s: %s",
                        pass_type, item.file_id, error,
                    )
                    self.repository.mark_failed(item.id, error, MAX_ATTEMPTS)
                    failed += 1
            if pass_type == PASS_CLIP_EMBED and self._gallery_rebuild_pending:
                self._settle_gallery_rebuilds({item.user_id for item in items if item.user_id})
            return {"processed": processed, "failed": failed}

        for item in items:
            try:
                processor(item)
                self.repository.mark_done(item.id)
                processed += 1
            except Exception as exc:
                logger.exception(
                    "media_index: %s pass failed for file %s", pass_type, item.file_id
                )
                self.repository.mark_failed(item.id, str(exc), MAX_ATTEMPTS)
                failed += 1
        return {"processed": processed, "failed": failed}

    def status(self, pass_type: Optional[str] = None) -> Dict[str, object]:
        return {
            "queue": self.repository.queue_counts(pass_type),
            "tagged_files": self.repository.tagged_file_count(),
            "provenance": self.tagger_provider.provenance,
            "gallery_embedder": self.vision_embedder.embedder_slug,
            "prompt_embedder": self.text_embedding_provider.embedder_slug,
        }

    # --- Pass processors -------------------------------------------------------

    def _resolve_source_path(self, item: MediaIndexQueueItem) -> Optional[str]:
        """Image files are indexed directly; videos through their thumbnail."""
        relative = item.file_path if item.file_type == "IMAGE" else item.thumbnail_path
        if not relative:
            return None
        return self.file_service.get_full_path(relative)

    def _process_tags_item(self, item: MediaIndexQueueItem) -> None:
        source = self._resolve_source_path(item)
        if source is None:
            logger.debug(
                "media_index: no taggable source for file %s (%s), skipping",
                item.file_id,
                item.file_type,
            )
            return
        try:
            result = self.tagger_provider.tag_image_file(source)
        except FileNotFoundError:
            # Deleted between being queued and its turn coming up. There is
            # nothing to index and never will be, so retrying only makes noise.
            logger.warning(
                "media_index: file %s is gone (%s), skipping",
                item.file_id,
                source,
            )
            return
        self.repository.replace_file_tags(
            file_id=item.file_id,
            generation_id=item.generation_id,
            provenance=self.tagger_provider.provenance,
            tags=result.tags,
            ratings=result.ratings,
        )

    def _process_clip_batch(
        self, items: List[MediaIndexQueueItem]
    ) -> Dict[str, Optional[str]]:
        """Embed a claimed batch and upsert into the gallery vector store.

        Returns per-item outcomes: ``{item_id: None}`` on success (including
        source-less skips), ``{item_id: error}`` on failure. Images are opened
        per item so one unreadable file fails alone, then embedded in a single
        batched forward.
        """
        outcomes: Dict[str, Optional[str]] = {}
        embeddable: List[Tuple[MediaIndexQueueItem, Any]] = []

        from PIL import Image as PILImage

        for item in items:
            source = self._resolve_source_path(item)
            if source is None:
                logger.debug(
                    "media_index: no embeddable source for file %s (%s), skipping",
                    item.file_id,
                    item.file_type,
                )
                outcomes[item.id] = None
                continue
            try:
                with PILImage.open(source) as handle:
                    image = handle.convert("RGB")
                embeddable.append((item, image))
            except FileNotFoundError:
                logger.warning(
                    "media_index: file %s is gone (%s), skipping",
                    item.file_id,
                    source,
                )
                outcomes[item.id] = None
            except Exception as exc:
                outcomes[item.id] = str(exc)

        if not embeddable:
            return outcomes

        try:
            embeddings = self.vision_embedder.embed_images(
                [image for _, image in embeddable]
            )
        except Exception as exc:
            for item, _ in embeddable:
                outcomes[item.id] = str(exc)
            return outcomes

        for (item, _), embedding in zip(embeddable, embeddings):
            try:
                self.gallery_vector_store.add(
                    user_id=item.user_id,
                    file_id=item.file_id,
                    embedding=embedding,
                    metadata={"generation_id": item.generation_id or ""},
                )
                outcomes[item.id] = None
            except Exception as exc:
                outcomes[item.id] = str(exc)
        return outcomes

    def _process_prompt_embed_batch(
        self, items: List[MediaIndexQueueItem]
    ) -> Dict[str, Optional[str]]:
        """Embed each item's generation prompt and upsert into the gallery
        prompt vector store, one model forward for the whole batch.

        A file with no generation prompt (deleted generation, manually
        uploaded file, blank prompt) has nothing to embed - counted as a
        skip (``None``), same as a source-less image in `_process_clip_batch`,
        never a failure to retry.
        """
        outcomes: Dict[str, Optional[str]] = {}
        embeddable: List[Tuple[MediaIndexQueueItem, str]] = []

        for item in items:
            text = (item.prompt_text or "").strip()
            if not text:
                logger.debug(
                    "media_index: no prompt text for file %s, skipping", item.file_id
                )
                outcomes[item.id] = None
                continue
            embeddable.append((item, text))

        if not embeddable:
            return outcomes

        try:
            # `process_pending` is always run off the event loop (via
            # `asyncio.to_thread` - see the admin route and the automation
            # action), so this thread never has a running loop of its own to
            # bridge into.
            embeddings = asyncio.run(
                self.text_embedding_provider.embed([text for _, text in embeddable])
            )
        except Exception as exc:
            for item, _ in embeddable:
                outcomes[item.id] = str(exc)
            return outcomes

        for (item, text), embedding in zip(embeddable, embeddings):
            try:
                self.gallery_prompt_vector_store.add(
                    user_id=item.user_id,
                    file_id=item.file_id,
                    embedding=embedding,
                    prompt_text=text,
                    metadata={"generation_id": item.generation_id or ""},
                )
                outcomes[item.id] = None
            except Exception as exc:
                outcomes[item.id] = str(exc)
        return outcomes

    # --- Search ----------------------------------------------------------------

    @staticmethod
    def apply_relative_cutoff(
        hits: List[Dict[str, Any]], cutoff: float = RELATIVE_CUTOFF
    ) -> List[Dict[str, Any]]:
        """Keep hits within ``cutoff`` of the best similarity.

        SigLIP cosines are low in absolute terms, so relevance is relative to
        the top hit for this query - never an absolute floor.
        """
        if not hits:
            return []
        top = max(hit["similarity"] for hit in hits)
        return [hit for hit in hits if hit["similarity"] >= top - cutoff]

    def search_gallery(
        self, user_id: str, query: str, limit: int = SEMANTIC_TOP_K
    ) -> List[Dict[str, Any]]:
        """Rank a user's gallery files against a free-text query.

        Returns ``[{file_id, generation_id, similarity}]`` best-first, top-K
        with the relative cutoff applied. Synchronous and CPU-bound (first
        call may load the model); async callers should wrap in a thread.
        """
        query = (query or "").strip()
        if not query:
            return []
        self._heal_stale_gallery_collection(user_id)
        embedding = self.vision_embedder.embed_texts([query])[0]
        hits = self.gallery_vector_store.search(user_id, embedding, limit=limit)
        return self.apply_relative_cutoff(hits)

    def gallery_collection_size(self, user_id: str) -> int:
        """Vector count in the user's active gallery collection.

        Lets a caller applying its own post-hoc filters (e.g. SQL filters
        that cannot be expressed as Chroma metadata) know the true ceiling
        for widening its query, rather than guessing when it has seen
        everything.
        """
        return self.gallery_vector_store.collection_size(user_id)

    def all_gallery_generation_ids(self, user_id: str) -> List[str]:
        """Every distinct generation id embedded in the user's collection.

        Ranking-free, so a caller can compute an exact post-filter total
        without sizing an ANN query to the whole collection.
        """
        return self.gallery_vector_store.all_generation_ids(user_id)

    def describe_files(self, file_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Thumbnail/path/type context for search hits (keyed by file id)."""
        return self.repository.file_summaries(file_ids)
