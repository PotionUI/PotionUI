from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

CONTROL_ENV = "E2E_STUB_ENGINE_CONTROL"
HANG_LIMIT_SECONDS = 600.0
OK = "ok"
FAIL = "fail"
HANG = "hang"


def parse_control(text: Optional[str]) -> tuple[str, str]:
    stripped = (text or "").strip()
    if not stripped:
        return OK, ""
    mode, _, rest = stripped.partition(" ")
    mode = mode.lower()
    if mode not in (OK, FAIL, HANG):
        return OK, ""
    return mode, rest.strip()


def read_control(path: Optional[os.PathLike]) -> tuple[str, str]:
    if path is None:
        return OK, ""
    try:
        return parse_control(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return OK, ""


def write_control(path: os.PathLike, mode: str, text: str = "") -> None:
    Path(path).write_text(f"{mode} {text}".strip() + "\n", encoding="utf-8")


def _picture():
    from PIL import Image

    return Image.new("RGB", (64, 64), (96, 64, 160))


class StubPipelineExecutor:
    def __init__(self, control_path: Optional[os.PathLike] = None, picture: Optional[Callable[[], Any]] = None):
        self.control_path = control_path
        self._picture = picture or _picture
        self._lock = threading.Lock()
        self._running: Optional[str] = None
        self._cancelled: set[str] = set()
        self._cancel_on_start: set[str] = set()
        self.calls: List[Dict[str, Any]] = []

    @property
    def running_generation_id(self) -> Optional[str]:
        return self._running

    def cancel(self, generation_id: str) -> bool:
        with self._lock:
            if self._running != generation_id:
                return False
            self._cancelled.add(generation_id)
            return True

    def cancel_on_start(self, generation_id: str) -> None:
        self._cancel_on_start.add(generation_id)

    def discard_cancel_on_start(self, generation_id: str) -> None:
        self._cancel_on_start.discard(generation_id)

    def generate(
        self,
        pipes: List[Dict[str, Any]],
        generation_outputs: Callable[[Any], None],
        generation_id: Optional[str] = None,
        cache_owner: Optional[str] = None,
        services: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        from src.pipelines.outputs import ErrorGenerationOutput, ImageGenerationOutput

        mode, text = read_control(self.control_path)
        self.calls.append({"generation_id": generation_id, "pipes": len(pipes or []), "mode": mode})
        with self._lock:
            self._running = generation_id
        try:
            if generation_id in self._cancel_on_start:
                return None
            if mode == FAIL:
                message = text or "The stub engine was told to fail."
                generation_outputs(ErrorGenerationOutput(error=message))
                raise RuntimeError(message)
            if mode == HANG:
                deadline = time.monotonic() + HANG_LIMIT_SECONDS
                while generation_id not in self._cancelled and time.monotonic() < deadline:
                    time.sleep(0.1)
                return None
            generation_outputs(
                ImageGenerationOutput(
                    image=self._picture(),
                    temporary=False,
                    seed=1,
                    resolution=(64, 64),
                )
            )
            return None
        finally:
            with self._lock:
                self._running = None
                self._cancelled.discard(generation_id)


def engine_factory(control_path: Optional[os.PathLike] = None) -> Callable[[], StubPipelineExecutor]:
    return lambda: StubPipelineExecutor(control_path)


def control_path_from_env() -> Optional[Path]:
    raw = os.environ.get(CONTROL_ENV)
    return Path(raw) if raw else None
