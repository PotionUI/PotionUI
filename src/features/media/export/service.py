from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from src.features.media.export.cache import ExportCache, cache_key
from src.features.media.export.image import convert_image
from src.features.media.export.jobs import ConvertJob, ConvertJobRegistry
from src.features.media.export.probe import (
    FormatSupport,
    probe_image_support,
    probe_video_support,
)
from src.features.media.export.specs import ConvertError, FormatUnavailable, ImageSpec, VideoSpec
from src.features.media.export.video import run_conversion
from src.platform.util.video_transcode import find_ffmpeg


class ExportConverter:
    def __init__(
        self,
        cache: ExportCache,
        jobs: Optional[ConvertJobRegistry] = None,
        ffmpeg_finder: Callable[[], Optional[str]] = find_ffmpeg,
        popen: Optional[Callable] = None,
    ):
        self.cache = cache
        self.jobs = jobs or ConvertJobRegistry()
        self._find_ffmpeg = ffmpeg_finder
        self._popen = popen

    def capabilities(self) -> dict:
        video = probe_video_support(self._find_ffmpeg())
        image = probe_image_support()
        return {
            "image": {fmt: item.to_dict() for fmt, item in image.items()},
            "video": {fmt: item.to_dict() for fmt, item in video.items()},
        }

    def convert_image(self, source: Path, spec: ImageSpec) -> Path:
        spec.validate()
        source = Path(source)
        key = cache_key(source, spec.options())
        hit = self.cache.get(key, spec.extension)
        if hit:
            return hit
        part = self.cache.part_path(key, spec.extension)
        try:
            convert_image(source, part, spec)
            return self.cache.commit(part, key, spec.extension)
        except BaseException:
            self.cache.discard(part)
            raise

    def convert_video(self, source: Path, spec: VideoSpec) -> ConvertJob:
        spec.validate()
        source = Path(source)
        ffmpeg = self._find_ffmpeg()
        support: FormatSupport = probe_video_support(ffmpeg)[spec.fmt]
        if not support.available:
            raise FormatUnavailable(support.reason or f"{spec.fmt} unavailable")
        key = cache_key(source, spec.options())
        hit = self.cache.get(key, spec.extension)

        def work(cancel: threading.Event, on_progress: Callable[[float], None]) -> Path:
            if hit:
                on_progress(1.0)
                return hit
            part = self.cache.part_path(key, spec.extension)
            kwargs = {"popen": self._popen} if self._popen else {}
            run_conversion(ffmpeg, source, part, spec, support, cancel, on_progress, **kwargs)
            return self.cache.commit(part, key, spec.extension)

        return self.jobs.submit(work)

    def cancel(self, job_id: str) -> bool:
        return self.jobs.cancel(job_id)

    def job(self, job_id: str) -> Optional[ConvertJob]:
        return self.jobs.get(job_id)
