import asyncio
import shutil
import time
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional, Type

from src.features.backends.base_backend import BaseBackend, ExecutionDevice
from src.features.backends.model_listing import BackendModel
from src.features.cloud.clock import Clock, MonotonicClock
from src.features.cloud.contracts import CLOUD_ENGINE, CloudBackendConfig, CloudError, CloudProvider
from src.features.cloud.bridge import CloudRunBridge
from src.features.cloud.http import CloudHttp
from src.features.cloud.repository import CloudCatalogRepository
from src.features.cloud.session import CloudRunSession
from src.platform.util.ids import generate_ulid
from src.platform.filesystem.model_types import CLOUD_MODEL_TYPE
from src.platform.observability.logger import logger
from src.pipelines.outputs import GenerationOutput

HEALTH_CACHE_SECONDS = 60.0
SCRATCH_ROOT_NAME = "potionui-cloud"
STALE_SCRATCH_SECONDS = 24 * 3600
EMPTY_SCRATCH_GRACE_SECONDS = 600


CLOUD_SERVICE = "CLOUD"
CLOUD_PIPE = "cloud_generate"


class CloudGenerationUnavailable(RuntimeError):
    pass


@dataclass
class CloudRun:
    executor: Any
    session: CloudRunSession
    scratch: Path
    notice_handed: bool = False


class CloudBackend(BaseBackend):
    execution_device: ClassVar[ExecutionDevice] = "remote"
    authoritative_listing: ClassVar[bool] = True
    provider_class: ClassVar[Type[CloudProvider]]

    def __init__(
        self,
        backend_config: CloudBackendConfig,
        *,
        catalog: Optional[CloudCatalogRepository] = None,
        clock: Optional[Clock] = None,
    ):
        super().__init__(backend_config)
        self.catalog = catalog or CloudCatalogRepository()
        self.clock = clock or MonotonicClock()
        self.http = CloudHttp.for_provider(self.provider_class, backend_config)
        self.provider = self.provider_class(backend_config, self.http)
        self._health: Optional[Dict[str, Any]] = None
        self._health_at = 0.0
        self._executor_factory: Optional[Callable[[], Any]] = None
        self._runs: Dict[str, CloudRun] = {}
        self._cancel_notices: Dict[str, str] = {}

    def bind_executor_factory(self, factory: Callable[[], Any]) -> None:
        self._executor_factory = factory

    @property
    def max_concurrent_runs(self) -> int:
        return self.config.max_parallel

    @property
    def driver(self) -> str:
        return self.config.driver

    def supports_model_listing(self) -> bool:
        return True

    async def list_models(self) -> List[BackendModel]:
        return [
            BackendModel(model_type=CLOUD_MODEL_TYPE, filename=entry.slug, ref=entry.slug)
            for entry in self.catalog.list_enabled(self.backend_id)
        ]

    def prepare_pipes(self, pipes: List[Dict[str, Any]], user_ref: str = "") -> List[Dict[str, Any]]:
        identity = {"backend_id": self.backend_id, "driver": self.driver}
        if user_ref:
            identity["user_ref"] = user_ref
        prepared = []
        for pipe in pipes:
            if pipe.get("name") == CLOUD_PIPE:
                config = {**(pipe.get("config") or {}), "cloud": identity}
                pipe = {**pipe, "config": config}
            prepared.append(pipe)
        return prepared

    async def start_generation(
        self,
        pipeline_data: Dict[str, Any],
        emit: Callable[[Optional[GenerationOutput]], None],
    ) -> str:
        generation_id = pipeline_data.get("generation_id") or generate_ulid()
        pipes = pipeline_data.get("pipes")
        if not pipes:
            raise ValueError("No pipeline configuration provided")
        executor = self._executor_factory() if self._executor_factory is not None else None
        if executor is None:
            raise CloudGenerationUnavailable("This cloud backend has no pipeline executor.")

        await self._sweep_scratch_off_loop()
        scratch = self._scratch_root() / generation_id
        session = CloudRunSession(
            provider=self.provider,
            catalog=self.catalog,
            backend_id=self.backend_id,
            generation_id=generation_id,
            timeout_seconds=self.config.timeout_seconds,
            clock=self.clock,
            scratch_dir=scratch,
        )
        run = CloudRun(executor=executor, session=session, scratch=scratch)
        self._runs[generation_id] = run
        services = {CLOUD_SERVICE: CloudRunBridge(session, asyncio.get_running_loop())}
        asyncio.create_task(self._run(generation_id, run, self.prepare_pipes(pipes, user_ref=pipeline_data.get("user_ref") or ""), emit, services))
        logger.info(f"[CLOUD_BACKEND] Started generation {generation_id}")
        return generation_id

    async def _run(
        self,
        generation_id: str,
        run: CloudRun,
        pipes: List[Dict[str, Any]],
        emit: Callable[[Optional[GenerationOutput]], None],
        services: Dict[str, Any],
    ) -> None:
        try:
            await asyncio.to_thread(run.executor.generate, pipes, emit, generation_id, None, services)
            logger.info(f"[CLOUD_BACKEND] Generation {generation_id} completed")
        except Exception as error:
            logger.error(f"[CLOUD_BACKEND] Generation {generation_id} failed: {type(error).__name__}")
        finally:
            self._runs.pop(generation_id, None)
            late_notice = run.session.cancel_notice
            if late_notice and not run.notice_handed:
                self._cancel_notices[generation_id] = late_notice
            discard = getattr(run.executor, "discard_cancel_on_start", None)
            if discard is not None:
                discard(generation_id)
            await self._sweep_scratch_off_loop(run.scratch)
            emit(None)

    def _scratch_root(self) -> Path:
        return Path(tempfile.gettempdir()) / SCRATCH_ROOT_NAME / self.backend_id

    async def _sweep_scratch_off_loop(self, finished: Optional[Path] = None) -> None:
        await asyncio.to_thread(self._sweep_scratch, finished)

    def _sweep_scratch(self, finished: Optional[Path] = None) -> None:
        if finished is not None and finished.is_dir() and not self._holds_files(finished):
            shutil.rmtree(finished, ignore_errors=True)
        root = self._scratch_root()
        if not root.is_dir():
            return
        now = time.time()
        for entry in root.iterdir():
            if entry.name in self._runs or not entry.is_dir():
                continue
            try:
                age = now - entry.stat().st_mtime
                empty = not self._holds_files(entry)
                if (empty and age > EMPTY_SCRATCH_GRACE_SECONDS) or age > STALE_SCRATCH_SECONDS:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue

    @staticmethod
    def _holds_files(folder: Path) -> bool:
        return any(path.is_file() for path in folder.rglob("*"))

    async def cancel_generation(self, generation_id: str) -> bool:
        run = self._runs.get(generation_id)
        if run is None:
            return False
        try:
            if not run.executor.cancel(generation_id):
                run.executor.cancel_on_start(generation_id)
            await run.session.cancel()
            notice = await run.session.settled_cancel_notice()
            if notice:
                self._cancel_notices[generation_id] = notice
                run.notice_handed = True
        except Exception as error:
            logger.error(f"[CLOUD_BACKEND] Error cancelling generation {generation_id}: {type(error).__name__}")
            return False
        return True

    def take_cancel_notice(self, generation_id: str) -> Optional[str]:
        return self._cancel_notices.pop(generation_id, None)

    async def health_check(self) -> Dict[str, Any]:
        now = self.clock.now()
        if self._health is not None and now - self._health_at < HEALTH_CACHE_SECONDS:
            return dict(self._health)
        try:
            health = await self.provider.check()
        except CloudError as error:
            result: Dict[str, Any] = {"status": "error", "engine": CLOUD_ENGINE, "error": error.user_message}
        except Exception as error:
            logger.warning(f"[CLOUD_BACKEND] Health check for {self.name} failed: {error}")
            result = {"status": "error", "engine": CLOUD_ENGINE, "error": "The provider could not be reached."}
        else:
            result = {"status": "available" if health.ok else "error", "engine": CLOUD_ENGINE}
            if health.message:
                result["message"] = health.message
            if not health.ok:
                result["error"] = health.message or "The provider reported a problem."
        self._health = result
        self._health_at = now
        return dict(result)

    async def get_system_info(self) -> Dict[str, Any]:
        counts = self.catalog.counts(self.backend_id)
        state = self.catalog.get_state(self.backend_id)
        return {
            "engine": CLOUD_ENGINE,
            "driver": self.driver,
            "provider": self.provider_class.label,
            "data_notice": self.provider_class.data_notice,
            "models_total": counts["total"],
            "models_enabled": counts["enabled"],
            "models_missing": counts["missing"],
            "last_refreshed_at": state.refreshed_at if state else None,
            "max_parallel": self.config.max_parallel,
        }

    async def close(self) -> None:
        await self._sweep_scratch_off_loop()
        await self.http.close()


def cloud_backend_class(provider_class: Type[CloudProvider]) -> Type[CloudBackend]:
    return type(
        f"{provider_class.__name__}Backend",
        (CloudBackend,),
        {"provider_class": provider_class, "__module__": CloudBackend.__module__},
    )
