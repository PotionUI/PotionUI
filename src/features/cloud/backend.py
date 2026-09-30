from typing import Any, Callable, ClassVar, Dict, List, Optional, Type

from src.features.backends.base_backend import BaseBackend, ExecutionDevice
from src.features.backends.model_listing import BackendModel
from src.features.cloud.clock import Clock, MonotonicClock
from src.features.cloud.contracts import CLOUD_ENGINE, CloudBackendConfig, CloudError, CloudProvider
from src.features.cloud.http import CloudHttp
from src.features.cloud.repository import CloudCatalogRepository
from src.platform.filesystem.model_types import CLOUD_MODEL_TYPE
from src.platform.observability.logger import logger
from src.pipelines.outputs import GenerationOutput

HEALTH_CACHE_SECONDS = 60.0


class CloudGenerationUnavailable(RuntimeError):
    pass


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

    async def start_generation(
        self,
        pipeline_data: Dict[str, Any],
        emit: Callable[[Optional[GenerationOutput]], None],
    ) -> str:
        raise CloudGenerationUnavailable("Generation on cloud backends is not available yet.")

    async def cancel_generation(self, generation_id: str) -> bool:
        return False

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
        await self.http.close()


def cloud_backend_class(provider_class: Type[CloudProvider]) -> Type[CloudBackend]:
    return type(
        f"{provider_class.__name__}Backend",
        (CloudBackend,),
        {"provider_class": provider_class, "__module__": CloudBackend.__module__},
    )
