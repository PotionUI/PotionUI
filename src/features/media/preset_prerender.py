"""Background pre-rendering of preset media thumbnails.

`MediaStore.get_preset_file` already renders-and-caches a size on first
request, so nothing is broken without this - but the first viewer of a
preset would otherwise pay for that render on the request path. This queue
front-runs that: whenever the preset catalogue changes (startup scan, admin
"reload preset", a plugin enable/disable that adds or drops a `presets:`
root - anything that goes through `PresetTemplateLoader`'s load/reload path),
it walks every preset's media (cover + gallery) and renders whatever isn't
already cached for the sizes the frontend actually requests.
"""

import logging
import queue
import threading
from typing import Any, Iterable, List, Optional

from src.features.media.store import MediaStore

logger = logging.getLogger(__name__)

# The sizes frontend surfaces request today (see `getPresetAssetURL` call
# sites) - `thumbnail` and `large` are only ever rendered on demand.
PRERENDER_SIZES = ("small", "medium")


class PresetMediaPrerenderQueue:
    """Serial, low-priority pre-renderer for preset media thumbnails.

    One dedicated worker thread drains the queue one file at a time - never a
    thread pool - so a large catalogue scan never competes with request-
    serving CPU for more than a single render at once. Idempotent by
    construction: `schedule_scan` only enqueues what `MediaStore.
    preset_render_is_current` reports as missing, so re-running it (another
    scan, a restart re-scan finding everything already rendered) does no
    work for files it already reached.
    """

    def __init__(self, media_store: MediaStore):
        self._media_store = media_store
        self._queue: "queue.Queue[tuple[str, str, str]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._worker_lock = threading.Lock()

    def schedule_scan(self, presets: Iterable[Any]) -> int:
        """Enqueue every not-yet-cached (preset, file, size) triple for `presets`.

        Returns the number newly enqueued. Logs one INFO summary line per
        call - the scan's-eye view of how much work this pass found.
        """
        scheduled = 0
        up_to_date = 0
        for preset in presets:
            preset_id = getattr(preset, "id", None)
            if not preset_id:
                continue
            for file_path in _preset_media_paths(preset):
                for size in PRERENDER_SIZES:
                    if self._media_store.preset_render_is_current(preset_id, file_path, size):
                        up_to_date += 1
                        continue
                    self._queue.put((preset_id, file_path, size))
                    scheduled += 1

        if scheduled:
            self._ensure_worker()
        logger.info(f"preset media: rendered {scheduled}, up to date {up_to_date}")
        return scheduled

    def _ensure_worker(self) -> None:
        with self._worker_lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._drain, name="preset-media-prerender", daemon=True
                )
                self._worker.start()

    def _drain(self) -> None:
        while True:
            try:
                preset_id, file_path, size = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                self._media_store.get_preset_file(preset_id, file_path, size)
                logger.debug(f"preset media: rendered {preset_id}/{file_path}@{size}")
            except Exception as e:
                logger.debug(
                    f"preset media: failed to pre-render {preset_id}/{file_path}@{size}: {e}"
                )
            finally:
                self._queue.task_done()


def _preset_media_paths(preset: Any) -> List[str]:
    """Cover + gallery source paths from a `PresetTemplate.media` dict.

    `media` is `manifest.media.model_dump(exclude_none=True)` (see
    `PresetTemplateLoader._load_preset_file`) - a plain dict, gallery items
    plain dicts too, not the pydantic `PresetMedia`/`GalleryItem` models.
    """
    media = getattr(preset, "media", None) or {}
    paths: List[str] = []

    cover = media.get("cover")
    if cover:
        paths.append(cover)

    for item in media.get("gallery") or []:
        src = item.get("src") if isinstance(item, dict) else getattr(item, "src", None)
        if src:
            paths.append(src)

    return paths
