"""Encryption at rest for stored credentials.

Values are wrapped in a self-describing envelope (``enc:v1:<token>``) so a read
path can tell ciphertext from a legacy plaintext value without consulting a
schema. The token is Fernet: AES-128-CBC with an HMAC-SHA256 tag and a fresh
random IV per message, so tampering is detected rather than yielding garbage.

Key material never comes from the database - a key stored next to the data it
protects protects nothing. Resolution order:

1. ``POTIONUI_SECRET_KEY`` (plus optional ``POTIONUI_SECRET_KEYS_RETIRED``).
2. The key file named by ``POTIONUI_SECRET_KEY_FILE``.
3. A key file beside the database, generated on first use.
"""

from __future__ import annotations

import logging
import os
import stat
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from src.platform.security.config import warn_if_insecure_permissions

logger = logging.getLogger(__name__)

ENVELOPE_PREFIX = "enc:v1:"

SECRET_KEY_FILENAME = "secret.key"

ENV_KEY = "POTIONUI_SECRET_KEY"
ENV_RETIRED_KEYS = "POTIONUI_SECRET_KEYS_RETIRED"
ENV_KEY_FILE = "POTIONUI_SECRET_KEY_FILE"

_KEY_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR

_KEY_FILE_HEADER = (
    "# PotionUI credential encryption key.\n"
    "# The first key below encrypts new values; any further keys are accepted\n"
    "# for decryption only (rotation). Losing this file makes every stored\n"
    "# credential unrecoverable - back it up, and keep it at mode 0600.\n"
)


class SecretDecryptionError(RuntimeError):
    """A stored value could not be decrypted with any configured key.

    Carries the location of the value, never the value or the key.
    """


class SecretKeyError(RuntimeError):
    """Key material is missing or malformed."""


class SecretCipher:
    """Encrypts and decrypts credential values with a keyring.

    The first key encrypts; every key decrypts, which is what makes a rotation
    window possible.
    """

    def __init__(self, keys: Sequence[bytes]):
        if not keys:
            raise SecretKeyError("A secret cipher needs at least one key.")
        fernets = []
        for index, key in enumerate(keys):
            try:
                fernets.append(Fernet(key))
            except (ValueError, TypeError) as exc:
                raise SecretKeyError(
                    f"Key #{index + 1} is not a valid encryption key "
                    f"({type(exc).__name__}). Expected a urlsafe-base64 32-byte "
                    f"key as produced by `python scripts/rotate_secret_key.py --generate`."
                ) from None
        self._multi = MultiFernet(fernets)
        self._primary = fernets[0]
        self._key_count = len(fernets)

    @staticmethod
    def is_encrypted(value: object) -> bool:
        return isinstance(value, str) and value.startswith(ENVELOPE_PREFIX)

    def encrypt(self, plaintext: str) -> str:
        """Wrap ``plaintext`` in an envelope. Already-encrypted input passes through."""
        if self.is_encrypted(plaintext):
            return plaintext
        token = self._primary.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"{ENVELOPE_PREFIX}{token}"

    def decrypt(self, value: str, *, context: str) -> str:
        """Unwrap an envelope.

        ``context`` names *where* the value lives (e.g. ``plugin_settings:foo/api_key``)
        so an operator can find the row. It must never contain the value itself.
        """
        if not self.is_encrypted(value):
            raise SecretDecryptionError(
                f"{context}: value is not an encrypted envelope."
            )
        token = value[len(ENVELOPE_PREFIX):].encode("ascii", errors="replace")
        try:
            return self._multi.decrypt(token).decode("utf-8")
        except (InvalidToken, ValueError):
            raise SecretDecryptionError(
                f"{context}: could not be decrypted with any configured key "
                f"({self._key_count} key(s) tried). Either the encryption key "
                f"changed or the stored value was tampered with. Restore the "
                f"original key, or re-enter the credential to overwrite it."
            ) from None

    def decrypt_if_encrypted(self, value: Optional[str], *, context: str) -> Optional[str]:
        """Decrypt enveloped values; return anything else unchanged.

        Values written before encryption existed are plaintext and stay readable.
        """
        if not self.is_encrypted(value):
            return value
        return self.decrypt(value, context=context)

    def can_decrypt(self, value: str) -> bool:
        """Whether ``value`` decrypts, without raising. For preflight reporting."""
        try:
            self.decrypt(value, context="probe")
            return True
        except SecretDecryptionError:
            return False

    def rotate(self, value: str, *, context: str) -> str:
        """Re-encrypt an enveloped value under the primary key.

        Decryption happens first and is allowed to raise: a value that cannot be
        read must not be replaced by a re-encryption of nothing.
        """
        plaintext = self.decrypt(value, context=context)
        token = self._primary.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"{ENVELOPE_PREFIX}{token}"


def generate_key() -> bytes:
    """A fresh key in the format the env vars and key file expect."""
    return Fernet.generate_key()


def default_key_path() -> Path:
    """Where the key lives when nothing names a location.

    Beside the database, so a scratch ``POTIONUI_DB_PATH`` gets its own key and
    can never read or overwrite the real one.
    """
    override = os.environ.get(ENV_KEY_FILE)
    if override:
        return Path(override)
    db_path = Path(os.environ.get("POTIONUI_DB_PATH", "storage/db.sqlite"))
    return db_path.parent / SECRET_KEY_FILENAME


def _parse_key_file(text: str) -> List[bytes]:
    keys = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        keys.append(stripped.encode("ascii"))
    return keys


def _write_key_file(path: Path, keys: Sequence[bytes]) -> None:
    """Write the keyring atomically at 0600.

    A same-directory temp file plus rename means a concurrent first boot can
    never observe a half-written key file.
    """
    body = _KEY_FILE_HEADER + "".join(f"{key.decode('ascii')}\n" for key in keys)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, _KEY_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    os.replace(tmp_name, path)
    try:
        os.chmod(path, _KEY_FILE_MODE)
    except OSError:
        pass


def write_key_file(path: Path, keys: Sequence[bytes]) -> None:
    """Persist a keyring, primary first. Used by the rotation script."""
    _write_key_file(path, keys)


def read_key_file(path: Path) -> List[bytes]:
    """Read a keyring from disk. Raises if the file exists but holds no key."""
    text = path.read_text(encoding="utf-8")
    keys = _parse_key_file(text)
    if not keys:
        raise SecretKeyError(
            f"The encryption key file at {path} contains no key. Restore it from "
            f"a backup, or delete it to have a new one generated - note that a "
            f"new key cannot read credentials encrypted with the old one."
        )
    return keys


def resolve_secret_keys(*, allow_generate: bool = True) -> List[bytes]:
    """Resolve the keyring from the environment or the key file.

    Generates and persists a key when none exists, so an ordinary install boots
    with encryption on and no configuration. Generation only ever happens when
    there is no key to lose.
    """
    env_key = os.environ.get(ENV_KEY, "").strip()
    if env_key:
        keys = [env_key.encode("ascii")]
        retired = os.environ.get(ENV_RETIRED_KEYS, "")
        keys.extend(
            part.strip().encode("ascii") for part in retired.split(",") if part.strip()
        )
        return keys

    path = default_key_path()
    if path.exists():
        warn_if_insecure_permissions(path, "credential encryption key")
        return read_key_file(path)

    if not allow_generate:
        raise SecretKeyError(
            f"No encryption key: {ENV_KEY} is unset and {path} does not exist."
        )

    new_key = generate_key()
    try:
        _write_key_file(path, [new_key])
    except OSError as exc:
        raise SecretKeyError(
            f"No encryption key is available and one could not be written to "
            f"{path} ({exc}). Set {ENV_KEY} or make that path writable. Refusing "
            f"to hold credentials with an in-memory key that would be lost on "
            f"restart, leaving every stored credential unreadable."
        ) from None
    logger.info(
        "Generated a credential encryption key at %s. Back this file up: without "
        "it, stored credentials cannot be decrypted.", path
    )
    return [new_key]


_cipher: Optional[SecretCipher] = None


def get_secret_cipher() -> SecretCipher:
    """The process-wide cipher, resolved on first use.

    Reached for the same way ``db`` is: repositories are constructed by plugins
    with no arguments, so there is nothing to inject through.
    """
    global _cipher
    if _cipher is None:
        _cipher = SecretCipher(resolve_secret_keys())
    return _cipher


def configure_secret_cipher(cipher: Optional[SecretCipher]) -> None:
    """Install (or clear, with ``None``) the process-wide cipher."""
    global _cipher
    _cipher = cipher
