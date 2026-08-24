"""Tests for the LLM config TTL cache (Stage 5 / B1 speed pass).

A chat turn re-fetches the same LLM configuration row 2-3x (pre-chat actions,
gateway calls, once per tool-loop iteration); LLMRepository.get_configuration
caches it briefly and invalidates on write.
"""

import time
from unittest.mock import MagicMock, patch

from src.features.llm.repository import LLMConfig, LLMRepository


def make_config(id_="cfg-1", **overrides):
    base = dict(
        id=id_, name="Test", type="ollama", enabled=True, base_url="http://x",
        model="m", system_message="sys",
    )
    base.update(overrides)
    return LLMConfig(**base)


class TestLLMConfigCache:
    def _make_repo(self) -> LLMRepository:
        repo = LLMRepository()
        repo.config_repo = MagicMock()
        return repo

    def test_second_get_within_ttl_is_a_cache_hit(self):
        repo = self._make_repo()
        repo.config_repo.get_by_id.return_value = MagicMock()
        repo._db_config_to_pydantic = MagicMock(return_value=make_config())

        repo.get_configuration("cfg-1")
        repo.get_configuration("cfg-1")
        repo.get_configuration("cfg-1")

        assert repo.config_repo.get_by_id.call_count == 1

    def test_different_config_ids_are_independent_cache_entries(self):
        repo = self._make_repo()
        repo.config_repo.get_by_id.return_value = MagicMock()
        repo._db_config_to_pydantic = MagicMock(
            side_effect=lambda db_config: make_config(id_="whichever")
        )

        repo.get_configuration("cfg-1")
        repo.get_configuration("cfg-2")

        assert repo.config_repo.get_by_id.call_count == 2

    def test_update_configuration_invalidates_the_cache(self):
        repo = self._make_repo()
        repo.config_repo.get_by_id.return_value = MagicMock()
        repo._db_config_to_pydantic = MagicMock(
            side_effect=[make_config(name="old"), make_config(name="new")]
        )
        repo.config_repo.exists.return_value = True
        repo.config_repo.update.return_value = True

        first = repo.get_configuration("cfg-1")
        assert first.name == "old"

        assert repo.update_configuration("cfg-1", make_config(name="new")) is True

        second = repo.get_configuration("cfg-1")
        assert second.name == "new"
        assert repo.config_repo.get_by_id.call_count == 2

    def test_delete_configuration_invalidates_the_cache(self):
        repo = self._make_repo()
        repo.config_repo.get_by_id.return_value = MagicMock()
        repo._db_config_to_pydantic = MagicMock(return_value=make_config())
        repo.config_repo.exists.return_value = True
        repo.config_repo.delete.return_value = True
        repo._default_provider = None  # not the default, so delete is allowed

        repo.get_configuration("cfg-1")
        assert repo.delete_configuration("cfg-1") is True

        repo.get_configuration("cfg-1")
        assert repo.config_repo.get_by_id.call_count == 2

    def test_cache_entry_expires_after_ttl(self):
        repo = self._make_repo()
        repo.config_repo.get_by_id.return_value = MagicMock()
        repo._db_config_to_pydantic = MagicMock(return_value=make_config())

        repo.get_configuration("cfg-1")
        assert repo.config_repo.get_by_id.call_count == 1

        with patch(
            "src.features.llm.ttl_cache.time.monotonic",
            return_value=time.monotonic() + 3600,
        ):
            repo.get_configuration("cfg-1")

        assert repo.config_repo.get_by_id.call_count == 2

    def test_missing_configuration_is_not_cached(self):
        repo = self._make_repo()
        repo.config_repo.get_by_id.return_value = None

        assert repo.get_configuration("missing") is None
        assert repo.get_configuration("missing") is None
        assert repo.config_repo.get_by_id.call_count == 2
