from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.presets.routes import PresetController
from src.platform.security.user import AccountType, User


def _user(account_type):
    return User(username="u", email="u@example.com", password_hash="x", account_type=account_type, id="u1")


class Links:
    def __init__(self):
        self.asked = []

    def mode_install(self, preset_id, user):
        self.asked.append((preset_id, user.account_type))
        if user.account_type != AccountType.ADMIN:
            return {}
        return {"cinemagraph": {"recipe_id": "modes-vid", "ready": False, "missing_bytes": 201}}

    def install_recipe_id(self, preset_id):
        return "modes-vid" if preset_id == "VID" else None


MODES = {"preset_id": "VID", "modes": [{"name": "video"}, {"name": "cinemagraph"}], "default_mode": "video"}


@pytest.mark.asyncio
async def test_modes_carry_an_install_summary_for_admins_only():
    controller = PresetController(Mock(), Mock(), recipe_links=Links())
    with patch("src.features.presets.routes.operations.refresh_mode_requirements", AsyncMock()), patch(
        "src.features.presets.routes.operations.get_available_modes", side_effect=lambda c, p: {**MODES, "modes": [dict(m) for m in MODES["modes"]]}
    ):
        admin = (await controller.get_available_modes("VID", _user(AccountType.ADMIN))).data
        regular = (await controller.get_available_modes("VID", _user(AccountType.USER))).data

    assert admin["modes"][1]["install"] == {"recipe_id": "modes-vid", "ready": False, "missing_bytes": 201}
    assert admin["modes"][0]["install"] is None
    assert [m["install"] for m in regular["modes"]] == [None, None]


@pytest.mark.asyncio
async def test_missing_mode_model_requirements_offer_install_mode():
    controller = PresetController(Mock(), Mock(), recipe_links=Links())
    results = {
        "results": [
            {"type": "model", "status": "missing", "mode": "cinemagraph", "action": {"kind": "open_downloader", "payload": {}}},
            {"type": "model", "status": "missing", "mode": None, "action": None},
            {"type": "binary", "status": "missing", "mode": "cinemagraph", "action": None},
            {"type": "model", "status": "ok", "mode": "video", "action": None},
        ]
    }
    with patch("src.features.presets.routes.operations.get_preset_requirements", AsyncMock(return_value=results)):
        data = (await controller.get_preset_requirements("VID")).data

    assert data["results"][0]["action"] == {"kind": "install_mode", "payload": {"preset_id": "VID", "mode": "cinemagraph", "recipe_id": "modes-vid"}}
    assert [r["action"] for r in data["results"][1:]] == [None, None, None]
