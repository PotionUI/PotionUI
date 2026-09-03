"""This importer accepts only ComfyUI's Export (API) workflow format - the
UI export (`Workflow -> Export`, not `Export (API)`) is rejected as early
as possible, at both `/presets/import/analyze` and `/presets/import`, with
the same teaching message pointing at the fix (see
`backend.preset_import.parser.UI_FORMAT_MESSAGE`).
"""

import pytest
from fastapi import HTTPException

from backend import api
from backend.preset_import.parser import UI_FORMAT_MESSAGE

_UI_EXPORT = {
    "nodes": [{"id": 1, "type": "KSampler"}],
    "links": [[1, 1, 0, 2, 0]],
}


@pytest.mark.asyncio
async def test_analyze_rejects_a_ui_export_with_the_teaching_message():
    body = api.AnalyzeWorkflowRequest(workflow=_UI_EXPORT)

    with pytest.raises(HTTPException) as exc_info:
        await api.analyze_workflow(body, current_user=None)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == UI_FORMAT_MESSAGE


@pytest.mark.asyncio
async def test_import_rejects_a_ui_export_with_the_teaching_message():
    body = api.ImportWorkflowRequest(
        workflow=_UI_EXPORT,
        model_family="SomeFamily",
        display_name="Some Preset",
    )

    with pytest.raises(HTTPException) as exc_info:
        await api.import_workflow(body, current_user=None)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == UI_FORMAT_MESSAGE
