"""LLMRepository reads the default provider through the shared settings
table (SettingRepository), not the retired configuration_repository stack."""

from src.features.llm.repository import LLMRepository
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
