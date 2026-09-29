import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional, Sequence

from src.features.content_safety.banned_words import (
    MAX_SCAN_CHARS,
    BannedWordsMatcher,
    collect_positive_texts,
    validate_entries,
)
from src.features.content_safety.constants import (
    ERROR_CHECK_UNAVAILABLE,
    EVENT_BANNED_PROMPT,
    EVENT_CHECK_UNAVAILABLE,
    EVENT_OUTPUT_BLOCKED,
    POLICY_ALLOWED,
    RESTRICTED_GROUP_ID,
    SETTING_BANNED_WORDS,
)
from src.features.content_safety.errors import ContentCheckUnavailable
from src.features.content_safety.gate import ContentGate
from src.features.content_safety.ledger_repository import ContentLedger
from src.features.content_safety.output_gate import (
    KIND_VIDEO,
    GateItem,
    GateOutcome,
    apply_verdicts,
    gate_items,
    is_unknown_output,
    mark,
)
from src.features.generation.error_classification import classification_for_code
from src.features.content_safety.policy import ContentPolicyResolver, EffectivePolicy
from src.pipelines.outputs import GenerationOutput

logger = logging.getLogger(__name__)

_BACKFILL_BATCH = 25


class ContentSafetyManager:
    def __init__(
        self,
        settings: Any,
        resolver: ContentPolicyResolver,
        ledger: ContentLedger,
        gate: ContentGate,
        file_service: Any = None,
        download_queue: Any = None,
        clock: Any = time.monotonic,
    ):
        self.settings = settings
        self.resolver = resolver
        self.ledger = ledger
        self.gate = gate
        self.file_service = file_service
        self.download_queue = download_queue
        self._clock = clock
        self._semaphore = asyncio.Semaphore(1)
        self._words_key: Optional[tuple] = None
        self._matcher = BannedWordsMatcher(())
        self._preview_rating: Dict[str, tuple] = {}
        self._backfill_task: Optional[asyncio.Task] = None
        self._backfill_running = False

    def policy_for(self, user_id: Optional[str]) -> EffectivePolicy:
        return self.resolver.resolve(user_id)

    def is_restricted(self, user_id: Optional[str]) -> bool:
        return self.resolver.resolve(user_id).restricted

    def preflight(self, user_id: Optional[str]) -> EffectivePolicy:
        policy = self.resolver.resolve(user_id)
        if policy.blocked and not self.gate.available():
            self.ledger.log_event(EVENT_CHECK_UNAVAILABLE, user_id=user_id, mode=policy.mode, detail={"stage": "preflight"})
            raise ContentCheckUnavailable(classification_for_code(ERROR_CHECK_UNAVAILABLE).summary)
        return policy

    def filter_paths(self, viewer_id: Optional[str], paths: Sequence[str]) -> set:
        unique = {path for path in paths if path}
        if not self.is_restricted(viewer_id):
            return unique
        states = self.ledger.states(unique)
        return {path for path in unique if states.get(path) == "safe"}

    def viewable_generation_ids(self, viewer_id: Optional[str], generation_ids: Sequence[str]) -> set:
        ids = {gid for gid in generation_ids if gid}
        if not self.is_restricted(viewer_id):
            return ids
        states = self.ledger.generation_states(ids)
        return {
            gid for gid in ids
            if states.get(gid) and all(state == "safe" for state in states[gid])
        }

    def matcher(self) -> BannedWordsMatcher:
        raw = self.settings.get_setting(SETTING_BANNED_WORDS, [])
        if validate_entries(raw) is not None:
            return self._matcher
        key = tuple(raw)
        if key != self._words_key:
            try:
                self._matcher = BannedWordsMatcher(key)
                self._words_key = key
            except Exception:
                logger.exception("banned words list could not be compiled; keeping the last good list")
        return self._matcher

    def find_banned_prompt(
        self,
        user_id: Optional[str],
        generation_id: Optional[str],
        prompts: Any,
        form_data: Any,
    ) -> Optional[int]:
        matcher = self.matcher()
        if matcher.empty:
            return None
        texts = collect_positive_texts(prompts, form_data)
        if any(len(text) > MAX_SCAN_CHARS for text in texts):
            index = -1
        else:
            index = matcher.first_match_in(texts)
        if index is not None:
            self.ledger.log_event(
                EVENT_BANNED_PROMPT, user_id=user_id, generation_id=generation_id, detail={"entry": index}
            )
        return index

    def _rate_items(self, items: Sequence[GateItem]) -> List[float]:
        flat: List[Any] = []
        owners: List[int] = []
        for index, item in enumerate(items):
            if item.kind == KIND_VIDEO:
                continue
            for image in item.images():
                flat.append(image)
                owners.append(index)
        scores: List[Optional[float]] = [None] * len(items)
        for owner, score in zip(owners, self.gate.rate_images(flat)):
            current = scores[owner]
            scores[owner] = score if current is None else max(current, score)
        for index, item in enumerate(items):
            if item.kind == KIND_VIDEO:
                scores[index] = self.gate.rate_video(item.video_file())
            elif scores[index] is None:
                raise ContentCheckUnavailable("an output carried no image to rate")
        return scores

    def _preview_throttled(self, generation_id: str) -> bool:
        state = self._preview_rating.get(generation_id)
        if state is None:
            return False
        finished_at, duration = state
        return self._clock() - finished_at < duration

    async def gate_output(
        self, generation_id: str, user_id: Optional[str], output: GenerationOutput
    ) -> GateOutcome:
        policy = self.resolver.resolve(user_id)
        if policy.mode == POLICY_ALLOWED:
            return GateOutcome(output)
        items = gate_items(output)
        if not items:
            if is_unknown_output(output):
                mark(output, False, False, suppressed=True)
            return GateOutcome(output)

        previewish = not any(item.final for item in items)
        if previewish and self._preview_throttled(generation_id):
            return GateOutcome(None)

        scores: Optional[List[float]] = None
        unavailable = False
        async with self._semaphore:
            started = self._clock()
            try:
                scores = await asyncio.to_thread(self._rate_items, items)
            except ContentCheckUnavailable:
                unavailable = True
                logger.warning("content check unavailable for generation %s", generation_id, exc_info=True)
            if previewish:
                self._preview_rating[generation_id] = (self._clock(), self._clock() - started)

        outcome = apply_verdicts(
            output, items, scores, self.ledger.threshold(), policy.mode, unavailable
        )
        if unavailable and any(item.final for item in items):
            self.ledger.log_event(
                EVENT_CHECK_UNAVAILABLE, user_id=user_id, generation_id=generation_id, mode=policy.mode
            )
        if outcome.blocked:
            for verdict in outcome.verdicts:
                if verdict.flagged and verdict.item.final:
                    self.ledger.log_event(
                        EVENT_OUTPUT_BLOCKED,
                        user_id=user_id,
                        generation_id=generation_id,
                        mode=policy.mode,
                        score=verdict.score,
                    )
        return outcome

    def record_saved(self, outcome: GateOutcome) -> None:
        for verdict in outcome.verdicts:
            if not verdict.item.final:
                continue
            saved = getattr(verdict.item.target, "_saved_path", None)
            if not saved:
                continue
            try:
                if verdict.score is None:
                    self.ledger.record_unrated(saved)
                else:
                    self.ledger.record_score(saved, verdict.score, rater=self.gate.rater)
            except Exception:
                logger.exception("could not record the content rating of %s", saved)

    def finish_generation(self, generation_id: str) -> None:
        self._preview_rating.pop(generation_id, None)

    def status(self) -> Dict[str, Any]:
        tagger = self.gate.tagger
        downloading = False
        if self.download_queue is not None:
            try:
                downloading = self.download_queue.find_active_download_for_repo(tagger.model_name) is not None
            except Exception:
                logger.debug("tagger download lookup failed", exc_info=True)
        progress = self.ledger.backfill_progress()
        return {
            "policy": self.resolver.instance_policy(),
            "tagger": {
                "present": self.gate.available(),
                "device": tagger.device,
                "downloading": downloading,
            },
            "backfill": {**progress, "running": self._backfill_running},
        }

    def needs_backfill(self) -> bool:
        if self.resolver.instance_policy() != POLICY_ALLOWED:
            return True
        try:
            return bool(self.resolver.groups.get_group_members(RESTRICTED_GROUP_ID))
        except Exception:
            logger.debug("restricted group lookup failed", exc_info=True)
            return False

    def start_backfill(self) -> bool:
        if self._backfill_running or not self.gate.available():
            return False
        self._backfill_running = True
        self._backfill_task = asyncio.create_task(self._backfill_loop())
        return True

    def start_backfill_if_needed(self) -> bool:
        return self.needs_backfill() and self.start_backfill()

    async def stop(self) -> None:
        task = self._backfill_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._backfill_running = False

    async def _backfill_loop(self) -> None:
        after = ""
        try:
            while True:
                rows = await asyncio.to_thread(self.ledger.unrated_files, _BACKFILL_BATCH, after)
                if not rows:
                    break
                after = rows[-1]["id"]
                async with self._semaphore:
                    await asyncio.to_thread(self._rate_backfill_rows, rows)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("content rating backfill stopped")
        finally:
            self._backfill_running = False

    def _rate_backfill_rows(self, rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            try:
                score = self._rate_backfill_row(row)
            except ContentCheckUnavailable:
                logger.debug("backfill could not rate %s", row["file_path"], exc_info=True)
                continue
            self.ledger.record_score(row["file_path"], score, rater=self.gate.rater, source="backfill")

    def _rate_backfill_row(self, row: Dict[str, Any]) -> float:
        if self.file_service is None:
            raise ContentCheckUnavailable("no file store")
        full = self.file_service.get_full_path(row["file_path"])
        if row["file_type"] == "VIDEO":
            if full and os.path.isfile(full):
                try:
                    return self.gate.rate_video(full)
                except ContentCheckUnavailable:
                    pass
            thumb = row.get("thumbnail_medium")
            if thumb:
                candidates = [
                    self.file_service.get_full_path(f"{os.path.dirname(row['file_path'])}/{thumb}"),
                    self.file_service.get_full_path(thumb),
                ]
                for candidate in candidates:
                    if candidate and os.path.isfile(candidate):
                        return self.gate.rate_image_file(candidate)
            raise ContentCheckUnavailable("video could not be read")
        if not full or not os.path.isfile(full):
            raise ContentCheckUnavailable("file is missing")
        return self.gate.rate_image_file(full)
