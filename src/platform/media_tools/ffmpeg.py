from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import threading
import time
from typing import Callable, Optional, Sequence

from src.platform.media_tools.types import FfmpegUnavailable, ToolCancelled, ToolFailed
from src.platform.util.video_transcode import find_ffmpeg

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 1800.0
POLL_SECONDS = 0.1
TERMINATE_GRACE_SECONDS = 1.0
STDERR_TAIL_CHARS = 2000

_CLOCK_RE = re.compile(r"^(\d+):(\d+):(\d+(?:\.\d+)?)$")

ProgressSink = Callable[[float, str], None]

_thread_cap_provider: Callable[[], int] = lambda: 0


def set_thread_cap_provider(provider: Optional[Callable[[], int]]) -> None:
    global _thread_cap_provider
    _thread_cap_provider = provider if provider is not None else (lambda: 0)


def current_thread_cap() -> int:
    try:
        return max(0, int(_thread_cap_provider()))
    except Exception:
        logger.exception("The ffmpeg thread cap could not be read")
        return 0


def apply_thread_cap(command: Sequence[str], threads: int) -> list[str]:
    command = [str(part) for part in command]
    if threads <= 0 or len(command) < 2 or "-threads" in command:
        return command
    count = str(threads)
    body = command[1:]
    capped: list[str] = []
    for part in body[:-1]:
        if part == "-i":
            capped += ["-threads", count]
        capped.append(part)
    last = body[-1]
    if not last.startswith("-") or last == "-":
        capped += ["-threads", count]
    capped.append(last)
    return [command[0], "-filter_threads", count, "-filter_complex_threads", count, *capped]


def parse_progress_line(line: str) -> Optional[float]:
    key, _, value = line.strip().partition("=")
    if key in ("out_time_us", "out_time_ms"):
        try:
            return max(0.0, int(value) / 1_000_000)
        except ValueError:
            return None
    if key == "out_time":
        found = _CLOCK_RE.match(value.strip())
        if found:
            hours, minutes, seconds = found.groups()
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return None


def _stop(process: "subprocess.Popen") -> None:
    process.terminate()
    try:
        process.wait(timeout=TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


class FfmpegRunner:
    def __init__(self, ffmpeg: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self._ffmpeg = ffmpeg
        self.timeout = timeout
        self.commands: list[list[str]] = []

    def cap_threads(self, command: Sequence[str]) -> list[str]:
        return apply_thread_cap(command, current_thread_cap())

    def binary(self) -> str:
        found = self._ffmpeg or find_ffmpeg()
        if not found:
            raise FfmpegUnavailable("ffmpeg is not available on this server")
        return found

    def run(
        self,
        args: Sequence[str],
        *,
        duration: Optional[float] = None,
        on_progress: Optional[ProgressSink] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> None:
        command = [
            self.binary(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-nostats", "-progress", "pipe:1", *[str(a) for a in args],
        ]
        command = self.cap_threads(command)
        self.commands.append(command)

        if is_cancelled is not None and is_cancelled():
            raise ToolCancelled()

        deadline = time.monotonic() + self.timeout
        with tempfile.TemporaryFile() as errors:
            try:
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=errors, stdin=subprocess.DEVNULL,
                    text=True, errors="replace", bufsize=1,
                )
            except OSError as exc:
                raise FfmpegUnavailable("ffmpeg could not be started on this server") from exc

            reader = threading.Thread(
                target=self._pump, args=(process, duration, on_progress), daemon=True,
            )
            reader.start()
            try:
                while True:
                    try:
                        process.wait(timeout=POLL_SECONDS)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    if is_cancelled is not None and is_cancelled():
                        _stop(process)
                        raise ToolCancelled()
                    if time.monotonic() >= deadline:
                        _stop(process)
                        raise ToolFailed("Editing timed out")
            except BaseException:
                if process.poll() is None:
                    _stop(process)
                raise
            finally:
                reader.join(timeout=TERMINATE_GRACE_SECONDS)
                if process.stdout is not None:
                    process.stdout.close()

            if process.returncode != 0:
                errors.seek(0)
                tail = errors.read()[-STDERR_TAIL_CHARS:].decode("utf-8", errors="replace")
                logger.error("ffmpeg exited %s: %s", process.returncode, tail)
                raise ToolFailed("Failed to encode the media")

    @staticmethod
    def _pump(process: "subprocess.Popen", duration: Optional[float], on_progress: Optional[ProgressSink]) -> None:
        stream = process.stdout
        if stream is None:
            return
        try:
            for line in stream:
                if on_progress is None or not duration or duration <= 0:
                    continue
                seconds = parse_progress_line(line)
                if seconds is not None:
                    on_progress(min(0.99, seconds / duration), "")
        except (OSError, ValueError):
            return
