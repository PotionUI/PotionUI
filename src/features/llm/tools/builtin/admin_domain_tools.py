import asyncio
import json
import logging
from typing import Any, Dict

from pydantic import ValidationError

from src.features.downloads.dto import QueueModelDownloadRequest
from src.features.downloads.exceptions import DownloadException
from src.features.llm.tools.base import ToolContext, ToolResult
from src.features.llm.tools.builtin.domain_support import (
    DomainTool,
    acting_user,
    bounded_int,
    failure,
    payload,
    required_text,
    run_controller,
    without,
)
from src.features.automation.dto import RunNowRequest
from src.features.providers.base_provider import ProviderCapability
from src.features.providers.registry import ensure_providers_discovered

logger = logging.getLogger(__name__)

SEARCH_DEFAULT = 10
SEARCH_MAX = 25
RUNS_DEFAULT = 5
RUNS_MAX = 20
DOWNLOADS_DEFAULT = 20
DOWNLOADS_MAX = 50
SEARCH_TIMEOUT_SECONDS = 20
DOWNLOAD_ARGUMENTS = (
    "url", "model_type", "filename", "subdir", "provider_id", "checksum_sha256", "tags", "destination_backend_id",
)


class AutomationTool(DomainTool):
    domain = "automation"
    group = "Automation"
    context_group = "library"
    admin_only = True

    def _automation_id_property(self) -> Dict[str, Any]:
        return {"type": "string", "description": "The automation id from list_automations."}


class ListAutomationsTool(AutomationTool):
    icon = "workflow"
    name = "list_automations"
    read_only = True
    user_description = "Lists the server's automations (administrators only)."

    @property
    def description(self) -> str:
        return "List the server's automations: id, name, description and whether each is enabled."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        result = await run_controller(self.service(context).list_automations(acting_user(context)))
        if not result.success:
            return result
        items = json.loads(result.data)
        return payload({"automations": [without(item, "graph") for item in items]})


class RunAutomationTool(AutomationTool):
    icon = "play"
    name = "run_automation"
    spends = True
    user_description = "Triggers an automation now (administrators only)."

    @property
    def description(self) -> str:
        return (
            "Run one automation now, as its manual trigger would. Optional 'node_id' picks a trigger node and "
            "'payload' is the data it starts with. Returns the run id: follow it with get_automation_status."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "automation_id": self._automation_id_property(),
                "node_id": {"type": "string"},
                "payload": {"type": "object"},
            },
            "required": ["automation_id"],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        automation_id = required_text(kwargs, "automation_id")
        if not automation_id:
            return failure("'automation_id' is required")
        body = RunNowRequest(node_id=kwargs.get("node_id") or None, payload=kwargs.get("payload") or None)
        return await run_controller(self.service(context).run_now(automation_id, body, acting_user(context)))


class GetAutomationStatusTool(AutomationTool):
    icon = "activity"
    name = "get_automation_status"
    read_only = True
    user_description = "Shows how an automation's runs are going (administrators only)."

    @property
    def description(self) -> str:
        return (
            "Check an automation's runs. With 'run_id' returns that run and its node results; without it "
            f"returns the latest runs ('limit' default {RUNS_DEFAULT}, at most {RUNS_MAX})."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "automation_id": self._automation_id_property(),
                "run_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": RUNS_MAX, "default": RUNS_DEFAULT},
            },
            "required": ["automation_id"],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        automation_id = required_text(kwargs, "automation_id")
        if not automation_id:
            return failure("'automation_id' is required")
        controller, user = self.service(context), acting_user(context)
        run_id = required_text(kwargs, "run_id")
        if run_id:
            return await run_controller(controller.get_run(automation_id, run_id, user))
        limit = bounded_int(kwargs, "limit", RUNS_DEFAULT, 1, RUNS_MAX)
        return await run_controller(controller.list_runs(automation_id, user, limit=limit, before=None))


class DownloadTool(DomainTool):
    domain = "downloads"
    group = "Models & presets"
    context_group = "library"
    admin_only = True


class SearchProviderModelsTool(DownloadTool):
    icon = "search"
    name = "search_provider_models"
    read_only = True
    user_description = "Searches the configured model marketplaces (administrators only)."

    @property
    def description(self) -> str:
        return (
            "Search the model marketplaces configured on this server (for example CivitAI or Hugging Face). "
            "Each result has provider_id, provider_model_id, name, model_type and download_url; pass "
            "download_url and provider_id to download_model. Results flagged adult are left out."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "provider_id": {"type": "string", "description": "One provider; omit to search every provider that can."},
                "model_type": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": SEARCH_MAX, "default": SEARCH_DEFAULT},
            },
            "required": ["query"],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        query = required_text(kwargs, "query")
        if not query:
            return failure("'query' is required")
        limit = bounded_int(kwargs, "limit", SEARCH_DEFAULT, 1, SEARCH_MAX)
        registry = await ensure_providers_discovered()
        wanted = required_text(kwargs, "provider_id")
        ids = [wanted] if wanted else [
            provider.get_metadata().id for provider in registry.get_providers_with_capability(ProviderCapability.SEARCH)
        ]
        if not ids:
            return failure("No model marketplace that can search is set up on this server.")
        model_type = kwargs.get("model_type") or None

        async def ask(provider_id):
            return await registry.search_models(provider_id, query, model_type=model_type, limit=limit)

        tasks = {provider_id: asyncio.ensure_future(ask(provider_id)) for provider_id in ids}
        await asyncio.wait(list(tasks.values()), timeout=SEARCH_TIMEOUT_SECONDS)
        results, unanswered = [], []
        for provider_id, task in tasks.items():
            if not task.done():
                task.cancel()
                unanswered.append(provider_id)
            elif task.exception() is not None:
                logger.warning("provider search failed for %s", provider_id, exc_info=task.exception())
                unanswered.append(provider_id)
            else:
                results.extend(item.to_dict() for item in task.result() if not item.nsfw)
        answer = {"results": [without(item, "thumbnail_url", "nsfw") for item in results[: limit * len(ids)]]}
        if unanswered:
            answer["note"] = f"No answer from: {', '.join(unanswered)}. Results from the others are shown."
        return payload(answer)


class DownloadModelTool(DownloadTool):
    icon = "download"
    name = "download_model"
    user_description = "Queues a model download on the server (administrators only)."

    @property
    def description(self) -> str:
        return (
            "Queue a model file for download onto this server from a url (usually a search_provider_models "
            "download_url). Optional: model_type, filename, subdir, provider_id, checksum_sha256, tags, "
            "destination_backend_id. Returns the download: follow it with get_download_status."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "model_type": {"type": "string"},
                "filename": {"type": "string"},
                "subdir": {"type": "string"},
                "provider_id": {"type": "string"},
                "checksum_sha256": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "destination_backend_id": {"type": "string"},
            },
            "required": ["url"],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        try:
            request = QueueModelDownloadRequest(**{
                key: kwargs[key] for key in DOWNLOAD_ARGUMENTS if kwargs.get(key) is not None
            })
        except ValidationError:
            return failure("'url' is required, and every other field must be text.")
        try:
            download = await self.service(context).queue_model_download(
                url=request.url,
                destination_dir=request.destination_dir,
                model_type=request.model_type,
                subdir=request.subdir,
                filename=request.filename,
                tags=request.tags,
                checksum_sha256=request.checksum_sha256,
                provider_id=request.provider_id,
                created_by=context.user_id,
                destination_backend_id=request.destination_backend_id,
            )
        except DownloadException as exc:
            return failure(str(exc))
        return payload(download.to_dict())


class GetDownloadStatusTool(DownloadTool):
    icon = "activity"
    name = "get_download_status"
    read_only = True
    user_description = "Shows the server's model downloads (administrators only)."

    @property
    def description(self) -> str:
        return (
            "Check model downloads. With 'download_id' returns that download's status and progress; without "
            f"it lists recent downloads ('status' filters, 'limit' default {DOWNLOADS_DEFAULT}, at most "
            f"{DOWNLOADS_MAX})."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "download_id": {"type": "string"},
                "status": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": DOWNLOADS_MAX, "default": DOWNLOADS_DEFAULT},
            },
            "required": [],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        queue = self.service(context)
        download_id = required_text(kwargs, "download_id")
        try:
            if download_id:
                download = await asyncio.to_thread(queue.get_download, download_id)
                return payload(download.to_dict())
            limit = bounded_int(kwargs, "limit", DOWNLOADS_DEFAULT, 1, DOWNLOADS_MAX)
            listing = await asyncio.to_thread(queue.list_downloads, status=kwargs.get("status") or None, limit=limit)
        except DownloadException as exc:
            return failure(str(exc))
        return payload(listing)


ADMIN_DOMAIN_TOOLS = (
    ListAutomationsTool,
    RunAutomationTool,
    GetAutomationStatusTool,
    SearchProviderModelsTool,
    DownloadModelTool,
    GetDownloadStatusTool,
)
