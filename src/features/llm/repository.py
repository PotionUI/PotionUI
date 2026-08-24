import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel
import json
from src.platform.database import db
from src.features.llm.records import LLMConfiguration
from src.features.llm.ttl_cache import TTLCache
from src.platform.security.secrets import get_secret_cipher, SecretDecryptionError
from src.platform.settings.repository import SettingRepository
from src.platform.util.ids import generate_ulid

# A chat turn re-reads the same LLM config 2-3x (pre-chat actions, gateway
# calls, once per tool-loop iteration). This row rarely changes mid-turn, so a
# few seconds of staleness is an easy win; writes below invalidate it directly
# so admin edits still land on the very next call.
_CONFIG_CACHE_TTL_SECONDS = 5.0

class LLMConfig(BaseModel):
    """Pydantic model for API compatibility"""
    id: str
    name: str
    type: str  # "ollama", "openai" (also used for OpenRouter, base_url pointed there), or "native"
    enabled: bool
    base_url: str
    api_key: Optional[str] = None
    model: str
    system_message: str
    temperature: float = 0.7
    max_tokens: int = 2000
    timeout: int = 30
    supports_vision: bool = False
    disable_system_prompt: bool = False
    memory_reflection: bool = True
    provider_options: Optional[Dict[str, Any]] = None
    api_key_unreadable: bool = False

class LLMConfigurationRepository:
    """An LLM configuration's `api_key` bills a real account, so it is encrypted
    at rest here - the same envelope the plugin and backend stores use."""

    @staticmethod
    def _decrypt(config: LLMConfiguration) -> LLMConfiguration:
        try:
            config.api_key = get_secret_cipher().decrypt_if_encrypted(
                config.api_key, context=f"llm_configurations:{config.id}/api_key"
            )
        except SecretDecryptionError as exc:
            # An unreadable credential must not take the whole listing down -
            # the admin screen it breaks is the very place the operator
            # re-enters it. The key is withheld, never passed through
            # encrypted, so any attempt to USE this configuration fails as
            # "no API key" rather than billing with garbage.
            logging.getLogger(__name__).warning("%s", exc)
            config.api_key = None
            config.api_key_unreadable = True
        return config

    @staticmethod
    def _encrypt(api_key: Optional[str]) -> Optional[str]:
        if not api_key:
            return api_key
        return get_secret_cipher().encrypt(api_key)

    def get_all(self) -> Dict[str, LLMConfiguration]:
        """Get all LLM configurations as a dictionary (id -> config)"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM llm_configurations ORDER BY name")
            configs = {}
            for row in cursor.fetchall():
                config = self._decrypt(LLMConfiguration.from_row(row))
                configs[config.id] = config
            return configs
    
    def get_by_id(self, config_id: str) -> Optional[LLMConfiguration]:
        """Get LLM configuration by ID"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM llm_configurations WHERE id = ?", (config_id,))
            row = cursor.fetchone()
            return self._decrypt(LLMConfiguration.from_row(row)) if row else None

    def iter_encrypted_api_keys(self) -> List[Dict[str, Any]]:
        """Raw (undecrypted) api_key values, for the preflight check and rotation."""
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT id, api_key FROM llm_configurations "
                "WHERE api_key IS NOT NULL AND api_key LIKE 'enc:%'"
            )
            return [dict(row) for row in cursor.fetchall()]

    def replace_api_key(self, config_id: str, stored_value: str) -> None:
        """Overwrite one row's stored ciphertext verbatim."""
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE llm_configurations SET api_key = ? WHERE id = ?",
                (stored_value, config_id),
            )
    
    def create(self, config: LLMConfiguration) -> bool:
        """Create new LLM configuration"""
        try:
            now = datetime.now()
            # Serialize provider_options to JSON string
            provider_options_json = json.dumps(config.provider_options) if config.provider_options else None
            with db.get_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO llm_configurations
                    (id, name, type, enabled, base_url, api_key, model, system_message,
                     temperature, max_tokens, timeout, supports_vision, disable_system_prompt,
                     memory_reflection, provider_options, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    config.id, config.name, config.type, config.enabled, config.base_url,
                    self._encrypt(config.api_key), config.model, config.system_message, config.temperature,
                    config.max_tokens, config.timeout, config.supports_vision,
                    config.disable_system_prompt, config.memory_reflection, provider_options_json,
                    now.isoformat(), now.isoformat()
                ))
            return True
        except Exception:
            return False
    
    def update(self, config_id: str, config: LLMConfiguration) -> bool:
        """Update existing LLM configuration"""
        try:
            now = datetime.now()
            # Serialize provider_options to JSON string
            provider_options_json = json.dumps(config.provider_options) if config.provider_options else None
            with db.get_cursor() as cursor:
                cursor.execute("""
                    UPDATE llm_configurations
                    SET name = ?, type = ?, enabled = ?, base_url = ?, api_key = ?,
                        model = ?, system_message = ?, temperature = ?, max_tokens = ?,
                        timeout = ?, supports_vision = ?, disable_system_prompt = ?,
                        memory_reflection = ?, provider_options = ?, updated_at = ?
                    WHERE id = ?
                """, (
                    config.name, config.type, config.enabled, config.base_url,
                    self._encrypt(config.api_key), config.model, config.system_message, config.temperature,
                    config.max_tokens, config.timeout, config.supports_vision,
                    config.disable_system_prompt, config.memory_reflection, provider_options_json,
                    now.isoformat(), config_id
                ))
                return cursor.rowcount > 0
        except Exception:
            return False
    
    def delete(self, config_id: str) -> bool:
        """Delete LLM configuration"""
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM llm_configurations WHERE id = ?", (config_id,))
            return cursor.rowcount > 0
    
    def exists(self, config_id: str) -> bool:
        """Check if configuration exists"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT 1 FROM llm_configurations WHERE id = ?", (config_id,))
            return cursor.fetchone() is not None


class LLMRepository:
    """High-level repository providing business logic for LLM operations"""
    
    def __init__(self):
        self.config_repo = LLMConfigurationRepository()
        self._default_provider = None
        self._load_default_provider()
        self._config_cache: TTLCache[str, Optional[LLMConfig]] = TTLCache(_CONFIG_CACHE_TTL_SECONDS)

    def _load_default_provider(self):
        """Load default provider from configuration"""
        try:
            setting = SettingRepository().get_setting_by_key('llm_default_provider')
            if setting:
                self._default_provider = setting.get_typed_value()
        except Exception:
            pass

    @property
    def default_provider(self) -> Optional[str]:
        """Get the default provider ID"""
        return self._default_provider

    def _db_config_to_pydantic(self, db_config: LLMConfiguration) -> LLMConfig:
        """Convert database model to Pydantic model for API compatibility"""
        return LLMConfig(
            id=db_config.id,
            name=db_config.name,
            type=db_config.type,
            enabled=db_config.enabled,
            base_url=db_config.base_url,
            api_key=db_config.api_key,
            model=db_config.model,
            system_message=db_config.system_message,
            temperature=db_config.temperature,
            max_tokens=db_config.max_tokens,
            timeout=db_config.timeout,
            supports_vision=db_config.supports_vision,
            disable_system_prompt=db_config.disable_system_prompt,
            memory_reflection=db_config.memory_reflection,
            provider_options=db_config.provider_options,
            api_key_unreadable=db_config.api_key_unreadable
        )

    def _pydantic_to_db_config(self, pydantic_config: LLMConfig) -> LLMConfiguration:
        """Convert Pydantic model to database model"""
        return LLMConfiguration(
            id=pydantic_config.id,
            name=pydantic_config.name,
            type=pydantic_config.type,
            enabled=pydantic_config.enabled,
            base_url=pydantic_config.base_url,
            api_key=pydantic_config.api_key,
            model=pydantic_config.model,
            system_message=pydantic_config.system_message,
            temperature=pydantic_config.temperature,
            max_tokens=pydantic_config.max_tokens,
            timeout=pydantic_config.timeout,
            supports_vision=pydantic_config.supports_vision,
            disable_system_prompt=pydantic_config.disable_system_prompt,
            memory_reflection=pydantic_config.memory_reflection,
            provider_options=pydantic_config.provider_options
        )

    # Configuration management methods
    def get_all_configurations(self) -> Dict[str, LLMConfig]:
        """Get all LLM configurations"""
        db_configs = self.config_repo.get_all()
        return {
            config_id: self._db_config_to_pydantic(config)
            for config_id, config in db_configs.items()
        }

    def get_configuration(self, config_id: str) -> Optional[LLMConfig]:
        """Get specific LLM configuration.

        Cached for a few seconds (see ``_CONFIG_CACHE_TTL_SECONDS``) since a
        single chat turn re-fetches the same row several times; writes below
        invalidate the entry immediately.
        """
        cached = self._config_cache.get(config_id)
        if cached is not None:
            return cached
        db_config = self.config_repo.get_by_id(config_id)
        config = self._db_config_to_pydantic(db_config) if db_config else None
        if config is not None:
            self._config_cache.set(config_id, config)
        return config

    def get_default_configuration(self) -> Optional[LLMConfig]:
        """Get the default LLM configuration"""
        if self.default_provider:
            return self.get_configuration(self.default_provider)
        return None

    def create_configuration(self, config: LLMConfig) -> bool:
        """Create a new LLM configuration"""
        try:
            if self.config_repo.exists(config.id):
                return False

            db_config = self._pydantic_to_db_config(config)
            created = self.config_repo.create(db_config)
            if created:
                self._config_cache.invalidate(config.id)
            return created
        except Exception:
            return False

    def update_configuration(self, config_id: str, config: LLMConfig) -> bool:
        """Update an existing LLM configuration"""
        try:
            if not self.config_repo.exists(config_id):
                return False

            db_config = self._pydantic_to_db_config(config)
            updated = self.config_repo.update(config_id, db_config)
            if updated:
                self._config_cache.invalidate(config_id)
            return updated
        except Exception:
            return False

    def delete_configuration(self, config_id: str) -> bool:
        """Delete an LLM configuration"""
        try:
            if not self.config_repo.exists(config_id):
                return False

            # Don't delete if it's the default
            if self.default_provider == config_id:
                return False

            deleted = self.config_repo.delete(config_id)
            if deleted:
                self._config_cache.invalidate(config_id)
            return deleted
        except Exception:
            return False

    def set_default_provider(self, config_id: str) -> bool:
        """Set the default LLM provider"""
        try:
            if not self.config_repo.exists(config_id):
                return False

            # Update the default provider in configurations
            self.config_repo.set('llm_default_provider', config_id, 'Default LLM provider configuration')
            self._default_provider = config_id
            return True
        except Exception:
            return False

    # User LLM assignment methods
    def assign_llm_to_user(self, user_id: str, llm_config_id: str) -> bool:
        """Assign an LLM configuration to a user"""
        try:
            # Check if configuration exists
            if not self.config_repo.exists(llm_config_id):
                return False

            with db.get_cursor() as cursor:
                # Check if assignment already exists
                cursor.execute(
                    "SELECT id FROM user_llms WHERE user_id = ? AND llm_config_id = ?",
                    (user_id, llm_config_id)
                )
                if cursor.fetchone():
                    return True  # Already assigned

                # Create assignment
                assignment_id = generate_ulid()
                cursor.execute("""
                    INSERT INTO user_llms (id, user_id, llm_config_id)
                    VALUES (?, ?, ?)
                """, (assignment_id, user_id, llm_config_id))
                return True
        except Exception:
            return False

    def unassign_llm_from_user(self, user_id: str, llm_config_id: str) -> bool:
        """Remove LLM configuration assignment from a user"""
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    "DELETE FROM user_llms WHERE user_id = ? AND llm_config_id = ?",
                    (user_id, llm_config_id)
                )
                return True
        except Exception:
            return False

    def get_user_llm_assignments(self, user_id: str) -> List[str]:
        """Get all LLM configuration IDs assigned to a user (direct + group assignments)"""
        try:
            with db.get_cursor() as cursor:
                cursor.execute("""
                    SELECT DISTINCT llm_config_id FROM (
                        SELECT llm_config_id FROM user_llms WHERE user_id = ?
                        UNION
                        SELECT ugl.llm_config_id FROM user_group_llms ugl
                        JOIN user_group_members ugm ON ugl.group_id = ugm.group_id
                        WHERE ugm.user_id = ?
                    )
                """, (user_id, user_id))
                results = cursor.fetchall()
                llm_config_ids = [row[0] for row in results]
                return llm_config_ids
        except Exception as e:
            print(f"ERROR: get_user_llm_assignments failed for user {user_id}: {e}")
            return []

    def get_user_llm_configurations(self, user_id: str) -> Dict[str, LLMConfiguration]:
        """Get all LLM configurations assigned to a user"""
        try:
            assigned_ids = self.get_user_llm_assignments(user_id)
            all_configs = self.config_repo.get_all()

            result = {
                config_id: config
                for config_id, config in all_configs.items()
                if config_id in assigned_ids
            }
            return result
        except Exception as e:
            print(f"ERROR: get_user_llm_configurations failed for user {user_id}: {e}")
            return {}

    def get_llm_users(self, llm_config_id: str) -> List[str]:
        """Get all user IDs assigned to an LLM configuration"""
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    "SELECT user_id FROM user_llms WHERE llm_config_id = ?",
                    (llm_config_id,)
                )
                return [row[0] for row in cursor.fetchall()]
        except Exception:
            return []

    def get_llm_assignment_summary(self) -> Dict[str, Dict[str, int]]:
        """Direct-user and group assignment counts per LLM configuration,
        batched (two GROUP BY queries, not one per configuration)."""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT llm_config_id, COUNT(*) as c FROM user_llms GROUP BY llm_config_id")
            direct = {row[0]: row[1] for row in cursor.fetchall()}
            cursor.execute("SELECT llm_config_id, COUNT(*) as c FROM user_group_llms GROUP BY llm_config_id")
            group = {row[0]: row[1] for row in cursor.fetchall()}
        return {
            config_id: {
                'assignment_count': direct.get(config_id, 0),
                'group_count': group.get(config_id, 0)
            }
            for config_id in (direct.keys() | group.keys())
        }

    def get_all_user_llm_assignments(self) -> Dict[str, List[str]]:
        """Get all user-LLM assignments as dict[user_id, List[llm_config_id]]"""
        try:
            with db.get_cursor() as cursor:
                cursor.execute("SELECT user_id, llm_config_id FROM user_llms ORDER BY user_id")
                assignments = {}
                for row in cursor.fetchall():
                    user_id, llm_config_id = row
                    if user_id not in assignments:
                        assignments[user_id] = []
                    assignments[user_id].append(llm_config_id)
                return assignments
        except Exception:
            return {}

    def is_llm_assigned_to_user(self, user_id: str, llm_config_id: str) -> bool:
        """Check if an LLM configuration is assigned to a user (direct + group)"""
        try:
            with db.get_cursor() as cursor:
                cursor.execute("""
                    SELECT 1 FROM (
                        SELECT llm_config_id FROM user_llms WHERE user_id = ? AND llm_config_id = ?
                        UNION
                        SELECT ugl.llm_config_id FROM user_group_llms ugl
                        JOIN user_group_members ugm ON ugl.group_id = ugm.group_id
                        WHERE ugm.user_id = ? AND ugl.llm_config_id = ?
                    ) LIMIT 1
                """, (user_id, llm_config_id, user_id, llm_config_id))
                return cursor.fetchone() is not None
        except Exception:
            return False


# Global repository instances
llm_config_repo = LLMConfigurationRepository()
llm_repository = LLMRepository()