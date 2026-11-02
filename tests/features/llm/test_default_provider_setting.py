"""LLMRepository reads the default provider through the shared settings
table (SettingRepository), not the retired configuration_repository stack."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.llm import routes as llm_mod
from src.features.llm.repository import LLMConfig, LLMRepository
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from src.platform.settings.records import SettingValueType
from src.platform.settings.repository import SettingRepository


def test_default_provider_is_none_when_setting_unset(mock_db):
    repo = LLMRepository()

    assert repo.default_provider is None


def test_default_provider_is_read_from_settings_table(mock_db):
    SettingRepository().create_setting(
        key="llm_default_provider",
        value="cfg-42",
        value_type=SettingValueType.STRING,
    )

    repo = LLMRepository()

    assert repo.default_provider == "cfg-42"


def _config(id_="cfg-1"):
    return LLMConfig(
        id=id_, name=id_, type="ollama", enabled=True, base_url="http://x",
        model="m", system_message="sys",
    )


def test_set_default_provider_survives_a_new_repository(mock_db):
    repo = LLMRepository()
    repo.create_configuration(_config("cfg-1"))
    repo.create_configuration(_config("cfg-2"))

    assert repo.set_default_provider("cfg-1") is True
    assert repo.set_default_provider("cfg-2") is True

    assert repo.default_provider == "cfg-2"
    assert LLMRepository().default_provider == "cfg-2"


def test_set_default_provider_rejects_unknown_id(mock_db):
    repo = LLMRepository()

    assert repo.set_default_provider("missing") is False
    assert repo.default_provider is None
    assert LLMRepository().default_provider is None


def test_set_default_provider_reports_a_storage_failure(mock_db):
    repo = LLMRepository()
    repo.create_configuration(_config("cfg-1"))

    with patch.object(
        SettingRepository, "update_setting_value_by_key", side_effect=sqlite3.OperationalError("locked")
    ):
        assert repo.set_default_provider("cfg-1") is False

    assert repo.default_provider is None


def _client(repo):
    controller = llm_mod.LLMController(
        llm_repository=repo,
        llm_service=Mock(),
        settings=Mock(),
        plugin_registry=Mock(),
        profile_resolver=Mock(),
    )
    app = FastAPI()
    app.include_router(llm_mod.build_router(SimpleNamespace(llm_controller=controller)))
    admin = User(
        username="a", email="a@example.com", password_hash="x",
        account_type=AccountType.ADMIN, id="user-1",
    )
    app.dependency_overrides[get_current_active_user] = lambda: admin
    return TestClient(app, raise_server_exceptions=False)


def test_set_default_route_persists_and_reports_success(mock_db):
    repo = LLMRepository()
    repo.create_configuration(_config("cfg-1"))

    response = _client(repo).post("/api/llm/configurations/cfg-1/set-default")

    assert response.status_code == 200
    assert response.json()["data"]["default_provider"] == "cfg-1"
    assert LLMRepository().default_provider == "cfg-1"


def test_set_default_route_unknown_id_is_not_found(mock_db):
    response = _client(LLMRepository()).post("/api/llm/configurations/missing/set-default")

    body = response.json()
    assert body["success"] is False
    assert body["error"] == "configuration_not_found"
    assert LLMRepository().default_provider is None


def test_set_default_route_reports_a_storage_failure(mock_db):
    repo = LLMRepository()
    repo.create_configuration(_config("cfg-1"))

    with patch.object(
        SettingRepository, "update_setting_value_by_key", side_effect=sqlite3.OperationalError("locked")
    ):
        response = _client(repo).post("/api/llm/configurations/cfg-1/set-default")

    body = response.json()
    assert body["success"] is False
    assert body["error"] == "set_default_failed"
    assert LLMRepository().default_provider is None
