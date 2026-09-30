import asyncio
import logging
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

import yaml

from src.plugin_api.cloud import (
    CloudBackendConfig,
    CloudError,
    CloudHealth,
    CloudHttp,
    CloudJob,
    CloudModelSpec,
    CloudProvider,
    CloudRequest,
    parse_retry_after,
    spec_problems,
)

from .config import DEFAULT_BASE_URL, OpenRouterConfig
from .mapping import build_body, endpoint_list, model_spec, parse_result

logger = logging.getLogger(__name__)

SUGGESTIONS_FILE = Path(__file__).resolve().parents[1] / "cloud_models.yml"
ENDPOINT_LOOKUPS_AT_ONCE = 4

DATA_NOTICE = (
    "Your prompts, any pictures you add and the settings you choose are sent to OpenRouter (openrouter.ai). "
    "OpenRouter passes them on to the company that runs the chosen model, and which company that is depends on "
    "the model and on the routing settings of this backend, so the data policy of that company applies as well. "
    "PotionUI downloads each finished picture straight away and does not rely on OpenRouter to keep it. "
    "No user name or email is sent. If an administrator turns on the anonymous user id, an anonymous id is sent "
    "that cannot be traced back to a person without this server's secret key."
)


class OpenRouterProvider(CloudProvider):
    key: ClassVar[str] = "openrouter"
    label: ClassVar[str] = "OpenRouter"
    config_class: ClassVar[type[CloudBackendConfig]] = OpenRouterConfig
    data_notice: ClassVar[str] = DATA_NOTICE
    supports_cancel: ClassVar[bool] = False
    idempotent_submit: ClassVar[bool] = False

    @classmethod
    def api_base_url(cls, config: CloudBackendConfig) -> str:
        return (getattr(config, "base_url", "") or DEFAULT_BASE_URL).rstrip("/")

    @classmethod
    def auth_headers(cls, config: CloudBackendConfig) -> Mapping[str, str]:
        key = getattr(config, "api_key", "")
        return {"Authorization": f"Bearer {key}"} if key else {}

    def attribution_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self.config.app_url:
            headers["HTTP-Referer"] = self.config.app_url
        if self.config.app_title:
            headers["X-OpenRouter-Title"] = self.config.app_title
        return headers

    def suggested_model_ids(self) -> Tuple[str, ...]:
        try:
            data = yaml.safe_load(SUGGESTIONS_FILE.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return ()
        return tuple(item for item in data.get("suggested", []) if isinstance(item, str))

    def map_error(self, status: int, headers: Mapping[str, str], body: Any) -> Optional[CloudError]:
        error = body.get("error") if isinstance(body, dict) else None
        error = error if isinstance(error, dict) else {}
        metadata = error.get("metadata") if isinstance(error.get("metadata"), dict) else {}
        message = str(error.get("message") or "")[:300]
        retry_after = parse_retry_after(headers.get("retry-after"))
        if status == 402:
            return CloudError(
                "credits", "The OpenRouter account is out of credits. An administrator can add credits at openrouter.ai.",
                detail=f"HTTP 402: {message}", retry_after_s=retry_after,
            )
        if status == 403:
            reasons = metadata.get("reasons")
            if reasons:
                listed = ", ".join(str(reason) for reason in reasons) if isinstance(reasons, list) else str(reasons)
                facts = [f"reasons: {listed}"]
                for label, field in (("provider", "provider_name"), ("model", "model_slug")):
                    if metadata.get(field):
                        facts.append(f"{label}: {metadata[field]}")
                return CloudError(
                    "refused", "The model's content filter refused this request. Try a different prompt or picture.",
                    detail="moderation refused (" + "; ".join(facts) + ")",
                )
            return CloudError(
                "refused", "OpenRouter refused this request. The key may not be allowed to use this model.",
                detail=f"HTTP 403: {message}",
            )
        if status == 502:
            return CloudError(
                "unavailable", "The model is down at the moment. Try again shortly.",
                detail=f"HTTP 502: {message}", retry_after_s=retry_after,
            )
        if status == 503:
            return CloudError(
                "unavailable", "No provider could serve this request with the current routing settings.",
                detail=f"HTTP 503: {message}", retry_after_s=retry_after,
            )
        if status in (400, 422):
            return CloudError(
                "invalid_request", "OpenRouter rejected the request settings.", detail=f"HTTP {status}: {message}",
            )
        return None

    async def _get(self, target: str) -> Any:
        return await self.http.request_json("GET", target, headers=self.attribution_headers())

    async def _endpoints(self, model_id: str, gate: asyncio.Semaphore) -> List[Any]:
        async with gate:
            try:
                return endpoint_list(await self._get(f"/images/models/{model_id}/endpoints"))
            except CloudError as error:
                if error.kind in ("auth", "credits"):
                    raise
                logger.warning(f"[OPENROUTER] no endpoint details for {model_id}: {error.kind}")
                return []

    async def discover(self) -> List[CloudModelSpec]:
        payload = await self._get("/images/models")
        items = payload.get("data", []) if isinstance(payload, dict) else payload
        items = [item for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)] if isinstance(items, list) else []
        gate = asyncio.Semaphore(ENDPOINT_LOOKUPS_AT_ONCE)
        endpoints = await asyncio.gather(*(self._endpoints(item["id"], gate) for item in items))
        specs: List[CloudModelSpec] = []
        for item, details in zip(items, endpoints):
            spec = model_spec(item, details)
            if spec is None:
                continue
            problems = spec_problems(spec)
            if problems:
                logger.warning(f"[OPENROUTER] skipped {spec.provider_model_id}: {problems}")
                continue
            specs.append(spec)
        return specs

    async def submit(self, request: CloudRequest) -> CloudJob:
        body = await asyncio.to_thread(
            build_body, request, self.config.upstream_list(), request.user_ref if self.config.send_user_hash else None
        )
        payload = await self.http.request_json(
            "POST", "/images", json=body, headers=self.attribution_headers(),
            timeout_s=float(self.config.timeout_seconds),
        )
        result = parse_result(payload, request)
        job_id = result.provider_job_id or f"openrouter-{request.client_reference or 'image'}"
        return CloudJob(job_id=job_id, result=result)

    async def check(self) -> CloudHealth:
        try:
            await self._get("/images/models")
        except CloudError as error:
            if error.kind in ("auth", "credits", "refused"):
                return CloudHealth(ok=False, message=error.user_message)
            raise
        return CloudHealth(ok=True)
