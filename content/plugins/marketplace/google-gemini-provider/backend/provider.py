import asyncio
import logging
import re
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote

import yaml

from src.plugin_api.cloud import (
    CloudBackendConfig,
    CloudError,
    CloudHealth,
    CloudJob,
    CloudModelSpec,
    CloudProvider,
    CloudRequest,
    CloudStatus,
    parse_retry_after,
    spec_problems,
)

from .catalog import live_specs, static_specs
from .config import DEFAULT_BASE_URL, GoogleConfig
from .mapping import build_image_body, parse_image_result
from .video import FIRST_POLL_SECONDS, build_video_body, is_video_spec, job_handle, operation_name, parse_operation

logger = logging.getLogger(__name__)

SUGGESTIONS_FILE = Path(__file__).resolve().parents[1] / "cloud_models.yml"
PAGE_SIZE = 1000
MAX_PAGES = 10
FATAL_LISTING_KINDS = frozenset({"auth", "credits", "refused"})
MODEL_ID = re.compile(r"^[A-Za-z0-9._-]+$")
RETRY_DELAY = re.compile(r"^\s*(\d+(?:\.\d+)?)s\s*$")
KEY_REASONS = ("API_KEY_INVALID", "API_KEY_EXPIRED", "API_KEY_SERVICE_BLOCKED", "API_KEY_HTTP_REFERRER_BLOCKED", "API_KEY_IP_ADDRESS_BLOCKED")

DATA_NOTICE = (
    "Your prompts, any pictures you add and the settings you choose are sent to Google through the Gemini API "
    "(generativelanguage.googleapis.com), under the Gemini API terms that apply to the Google Cloud project of this "
    "backend's key. Those terms, and whether billing is turned on for the project, decide how Google may use and keep "
    "the data. Finished videos stay on Google's servers for two days; PotionUI downloads each picture and video straight "
    "away and does not rely on Google to keep it. No user name, email or user id is sent."
)


def _model_target(model_id: str, method: str) -> str:
    if not MODEL_ID.match(model_id):
        raise CloudError("invalid_request", "This model name cannot be used.", detail=f"unsafe model id {model_id!r}", request_sent=False)
    return f"/models/{quote(model_id, safe='._-')}:{method}"


def _details(error: Mapping[str, Any]) -> Tuple[List[str], Optional[float]]:
    reasons: List[str] = []
    delay: Optional[float] = None
    for item in error.get("details") or []:
        if not isinstance(item, dict):
            continue
        if item.get("reason"):
            reasons.append(str(item["reason"]))
        match = RETRY_DELAY.match(str(item.get("retryDelay") or ""))
        if match:
            delay = float(match.group(1))
    return reasons, delay


class GoogleProvider(CloudProvider):
    key: ClassVar[str] = "google"
    label: ClassVar[str] = "Google Gemini"
    config_class: ClassVar[type[CloudBackendConfig]] = GoogleConfig
    data_notice: ClassVar[str] = DATA_NOTICE
    supports_cancel: ClassVar[bool] = False
    idempotent_submit: ClassVar[bool] = False

    @classmethod
    def api_base_url(cls, config: CloudBackendConfig) -> str:
        return (getattr(config, "base_url", "") or DEFAULT_BASE_URL).rstrip("/")

    @classmethod
    def auth_headers(cls, config: CloudBackendConfig) -> Mapping[str, str]:
        key = getattr(config, "api_key", "")
        return {"x-goog-api-key": key} if key else {}

    def suggested_model_ids(self) -> Tuple[str, ...]:
        try:
            data = yaml.safe_load(SUGGESTIONS_FILE.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return ()
        listed = data.get("suggested", []) if isinstance(data, dict) else []
        return tuple(item for item in listed or [] if isinstance(item, str))

    def map_error(self, status: int, headers: Mapping[str, str], body: Any) -> Optional[CloudError]:
        error = body.get("error") if isinstance(body, dict) else None
        error = error if isinstance(error, dict) else {}
        message = str(error.get("message") or "")[:300]
        code = str(error.get("status") or "")
        reasons, delay = _details(error)
        retry_after = parse_retry_after(headers.get("retry-after"))
        if retry_after is None:
            retry_after = delay
        detail = f"HTTP {status} {code}: {message}".strip()
        key_problem = any(reason in KEY_REASONS for reason in reasons) or "api key" in message.lower()
        if status == 401 or (status in (400, 403) and key_problem):
            return CloudError("auth", "Google rejected the API key. Check it in Administration, Backends.", detail=detail)
        if status == 402:
            return CloudError(
                "credits", "The Google account has no prepaid credit left. An administrator can add credit in Google AI Studio.",
                detail=detail, retry_after_s=retry_after,
            )
        if status == 403:
            return CloudError("refused", "Google refused this request. The key may not be allowed to use this model.", detail=detail)
        if status == 400 and code == "FAILED_PRECONDITION":
            if "location" in message.lower() or "region" in message.lower():
                return CloudError("refused", "Google does not offer this model where this server is.", detail=detail)
            return CloudError(
                "credits", "This model needs billing turned on for the key's project. An administrator can turn it on in Google AI Studio.",
                detail=detail,
            )
        if status == 429:
            return CloudError(
                "rate_limited", "Google is limiting requests from this key. Try again shortly.",
                detail=detail, retry_after_s=retry_after,
            )
        if status == 404:
            return CloudError("invalid_request", "Google does not offer this model to this key.", detail=detail)
        if status == 400:
            return CloudError("invalid_request", "Google rejected the request settings.", detail=detail)
        if status == 503:
            return CloudError("unavailable", "The model is overloaded at the moment. Try again shortly.", detail=detail, retry_after_s=retry_after)
        if status == 504:
            return CloudError("timeout", "Google took too long to answer.", detail=detail)
        if status >= 500:
            return CloudError("unavailable", "Google had a problem on its side. Try again shortly.", detail=detail, retry_after_s=retry_after)
        return None

    async def _list_models(self) -> List[Any]:
        items: List[Any] = []
        token: Optional[str] = None
        for _ in range(MAX_PAGES):
            params: Dict[str, Any] = {"pageSize": PAGE_SIZE}
            if token:
                params["pageToken"] = token
            payload = await self.http.request_json("GET", "/models", params=params)
            if not isinstance(payload, dict):
                break
            page = payload.get("models")
            if isinstance(page, list):
                items.extend(page)
            token = payload.get("nextPageToken") if isinstance(payload.get("nextPageToken"), str) else None
            if not token:
                break
        return items

    @staticmethod
    def _valid(specs: List[CloudModelSpec]) -> List[CloudModelSpec]:
        kept = []
        for spec in specs:
            problems = spec_problems(spec)
            if problems:
                logger.warning(f"[GOOGLE] skipped {spec.provider_model_id}: {problems}")
                continue
            kept.append(spec)
        return kept

    async def discover(self) -> List[CloudModelSpec]:
        try:
            items = await self._list_models()
        except CloudError as error:
            if error.kind in FATAL_LISTING_KINDS:
                raise
            logger.warning(f"[GOOGLE] model listing failed ({error.kind}); using the built-in model list")
            return self._valid(static_specs())
        specs = live_specs(items)
        if not specs:
            logger.warning("[GOOGLE] the model listing named no image or video models; using the built-in model list")
            return self._valid(static_specs())
        return self._valid(specs)

    async def submit(self, request: CloudRequest) -> CloudJob:
        if is_video_spec(request.model):
            return await self._submit_video(request)
        target = _model_target(request.model.provider_model_id, "generateContent")
        body = await asyncio.to_thread(build_image_body, request)
        payload = await self.http.request_json("POST", target, json=body, timeout_s=float(self.config.timeout_seconds))
        result = parse_image_result(payload, request)
        job_id = result.provider_job_id or f"google-{request.client_reference or 'image'}"
        return CloudJob(job_id=job_id, result=result)

    async def _submit_video(self, request: CloudRequest) -> CloudJob:
        target = _model_target(request.model.provider_model_id, "predictLongRunning")
        body = await asyncio.to_thread(build_video_body, request)
        payload = await self.http.request_json("POST", target, json=body, timeout_s=float(self.config.timeout_seconds))
        name = operation_name(payload)
        return CloudJob(job_id=name, handle=job_handle(request, name), poll_after_s=FIRST_POLL_SECONDS)

    async def poll(self, job: CloudJob) -> CloudStatus:
        payload = await self.http.request_json("GET", f"/{job.handle['operation']}")
        return parse_operation(self.http, payload, dict(job.handle), job.job_id)

    async def check(self) -> CloudHealth:
        try:
            await self.http.request_json("GET", "/models", params={"pageSize": 1})
        except CloudError as error:
            if error.kind in FATAL_LISTING_KINDS:
                return CloudHealth(ok=False, message=error.user_message)
            raise
        return CloudHealth(ok=True)
