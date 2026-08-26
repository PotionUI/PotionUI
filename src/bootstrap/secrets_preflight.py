"""Startup check on credential encryption.

Two things happen here, after the container exists (which is what makes the
engine config classes, and therefore the backend secret-field names, knowable):

1. Any backend credential still stored in the clear is encrypted.
2. Every stored envelope is probed against the current key, and anything that
   will not decrypt is reported by location.

The process is deliberately allowed to start when step 2 finds problems. A
self-hosted box that refuses to boot over an unreadable credential locks the
operator out of the very screen where they would fix it, and an unreadable value
is never silently used: each read raises, and a deliberate re-entry overwrites
it. What must not happen - silently returning an empty credential, or rewriting
an unreadable value under a new key - is prevented in the cipher itself.
"""

import json
import logging
from typing import List

from src.features.plugins import operations as plugin_operations
from src.platform.security.secrets import SecretKeyError, get_secret_cipher

logger = logging.getLogger(__name__)


def run_secret_preflight(
    plugin_repository,
    backend_config_manager,
    llm_config_repository=None,
    plugin_registry=None,
) -> List[str]:
    """Encrypt what is still plaintext, then report what will not decrypt.

    Returns the locations that failed to decrypt, so a caller (or a test) can
    assert on them. Locations only - a value never appears in the return, in a
    log line, or in an exception message.
    """
    try:
        cipher = get_secret_cipher()
    except SecretKeyError as exc:
        logger.error(
            "Credential encryption is unavailable: %s Stored credentials cannot "
            "be read or written until this is resolved.", exc
        )
        return []

    if plugin_registry is not None:
        try:
            promoted = plugin_operations.encrypt_declared_secrets(plugin_repository, plugin_registry)
            if promoted:
                logger.info(
                    "Encrypted %d plugin setting(s) their manifest declares secret "
                    "but that were stored unflagged.", promoted
                )
        except Exception as exc:
            logger.error("Could not encrypt manifest-declared plugin secrets: %s", exc)

    try:
        rewritten = backend_config_manager.encrypt_stored_credentials()
        if rewritten:
            logger.info("Encrypted credentials for %d backend(s) at rest.", rewritten)
    except Exception as exc:
        logger.error("Could not encrypt backend credentials at rest: %s", exc)

    undecryptable: List[str] = []

    for row in plugin_repository.iter_encrypted_settings():
        if not cipher.can_decrypt(row['setting_value']):
            undecryptable.append(f"plugin_settings:{row['plugin_id']}/{row['setting_key']}")

    for row in backend_config_manager.backend_repository.iter_encrypted_configs():
        try:
            config = json.loads(row['config']) if row['config'] else {}
        except (TypeError, ValueError):
            continue
        for key, value in config.items():
            if cipher.is_encrypted(value) and not cipher.can_decrypt(value):
                undecryptable.append(f"backends:{row['id']}/{key}")

    if llm_config_repository is not None:
        for row in llm_config_repository.iter_encrypted_api_keys():
            if not cipher.can_decrypt(row['api_key']):
                undecryptable.append(f"llm_configurations:{row['id']}/api_key")

    if undecryptable:
        logger.error(
            "%d stored credential(s) cannot be decrypted with the current "
            "encryption key: %s. Restore the previous key (POTIONUI_SECRET_KEY, "
            "or the key file), add it to POTIONUI_SECRET_KEYS_RETIRED, or "
            "re-enter the affected credentials. They are NOT being used or "
            "overwritten in the meantime.",
            len(undecryptable), ", ".join(sorted(undecryptable)),
        )

    return undecryptable
