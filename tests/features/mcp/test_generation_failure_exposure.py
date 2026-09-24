import json
from unittest.mock import MagicMock, Mock

import pytest

from src.features.generation.failure import failure_from_output
from src.features.generation.records import Generation
from src.features.llm.tools.builtin.organize_gallery_tool import OrganizeGalleryTool
from src.features.llm.tools.governance_repository import ToolGovernanceRepository
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp.protocol import McpToolCollaborators, handle_method
from src.pipelines.outputs import ErrorGenerationOutput

RAW_ERROR = "FileNotFoundError: [Errno 2] No such file or directory: '/srv/models/private/unet.safetensors'"


def _failed_generation():
    columns = failure_from_output(ErrorGenerationOutput(
        error=RAW_ERROR,
        detail='Traceback (most recent call last):\n  File "/srv/app/src/loader.py", line 4',
    )).columns()
    data = Generation(
        id="gen-1", preset_id="p1", form_data={"prompt": "a fox"}, user_id="user-1", status="failed", **columns,
    ).to_dict()
    data.update({"preset_name": "SDXL", "tags": [], "files": []})
    return data


@pytest.mark.asyncio
async def test_an_mcp_client_sees_only_the_safe_failure_of_a_generation(mcp_db):
    registry = ToolRegistry()
    registry.register(OrganizeGalleryTool())
    history_facade = MagicMock()
    history_facade.get_by_id.return_value = _failed_generation()
    llm_repository = Mock()
    llm_repository.get_default_configuration.return_value = None
    collaborators = McpToolCollaborators(
        tool_registry=registry,
        tool_governance_repository=ToolGovernanceRepository(),
        llm_repository=llm_repository,
        generation_history_facade=history_facade,
        plugin_registry=Mock(),
    )

    result = await handle_method(
        collaborators,
        "tools/call",
        {"name": "organize_gallery", "arguments": {"operation": "get", "generation_id": "gen-1"}},
        "user-1",
    )

    text = result["content"][0]["text"]
    payload = json.loads(text)
    assert payload["error_code"] == "missing_model_file"
    assert payload["error_id"] == "gen-1"
    for marker in ("/srv/", "Traceback", "Errno", "FileNotFoundError"):
        assert marker not in text
