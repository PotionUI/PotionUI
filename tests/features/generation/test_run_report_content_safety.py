from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.features.generation.routes import GenerationController

REPORT = {
    "status_history": [{"step": "sampling"}],
    "artifacts": [{"artifact_type": "image", "artifact_data": {"image": {"$media": "run_report_artifact"}}}],
    "plugin_outputs": {"plugin_preview": {"message": {"image": "data:image/png;base64,AAAA"}}},
}


def controller(restricted):
    recorder = MagicMock()
    recorder.get_report.return_value = dict(REPORT)
    facade = MagicMock()
    facade.query.content_safety = SimpleNamespace(is_restricted=lambda user_id: restricted)
    return GenerationController(MagicMock(), facade, MagicMock(), recorder)


async def report_for(restricted):
    generation = SimpleNamespace(user_id="u1", form_data={"prompt": "p"})
    user = SimpleNamespace(id="u1", account_type="USER")
    with patch("src.features.generation.routes.generation_repo.get_by_id", return_value=generation):
        response = await controller(restricted).get_run_report("g1", user)
    return response.data["run_report"]


@pytest.mark.asyncio
async def test_restricted_owner_gets_the_report_without_artifact_media():
    report = await report_for(True)

    assert report["artifacts"] == []
    assert report["plugin_outputs"] == {}
    assert report["status_history"] == REPORT["status_history"]


@pytest.mark.asyncio
async def test_unrestricted_owner_keeps_the_full_report():
    report = await report_for(False)

    assert report["artifacts"] == REPORT["artifacts"]
