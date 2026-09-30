import asyncio
from typing import Callable, Optional

from src.features.cloud.session import CloudRunSession
from src.pipelines.cloud import CloudRunOutcome, CloudRunProgress, CloudRunRequest


class CloudRunBridge:
    def __init__(self, session: CloudRunSession, loop: asyncio.AbstractEventLoop) -> None:
        self._session = session
        self._loop = loop

    def __repr__(self) -> str:
        return "CloudRunBridge()"

    def run_blocking(
        self,
        request: CloudRunRequest,
        *,
        on_progress: Optional[Callable[[CloudRunProgress], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> CloudRunOutcome:
        future = asyncio.run_coroutine_threadsafe(
            self._session.run(request, on_progress=on_progress, is_cancelled=is_cancelled),
            self._loop,
        )
        return future.result()
