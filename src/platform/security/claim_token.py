"""One-time setup token that gates claiming the instance from a remote origin.

When an instance is unclaimed, the first account created becomes its owner
(admin). A claim from the loopback interface is trusted (the operator is on the
box), but a claim arriving over the network must prove the operator's intent by
presenting a token that only someone with filesystem/console access to the
server can read.

Contract with the bootstrap CLI (`./potionui`)
----------------------------------------------
The token is a plain-text file, owner-read/write only (0600), at::

    <file_storage_directory>/{CLAIM_TOKEN_FILENAME}

``file_storage_directory`` is the ``file_storage_directory`` setting (default
``storage``). The backend generates and persists this file at startup while the
instance is unclaimed and deletes it once the instance is claimed. The bootstrap
CLI should read that path and print its contents so the operator can paste the
token into the setup form; the CLI never needs to create or validate it.
"""

from __future__ import annotations

import logging
import os
import secrets
import stat
import tempfile
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.platform.settings.settings import Settings

logger = logging.getLogger(__name__)

# File name under the file storage directory. Part of the bootstrap-CLI contract
# (see module docstring); keep it stable.
CLAIM_TOKEN_FILENAME = "setup_claim_token"

# Owner read/write only (0600). A token readable by other users is a leak.
_TOKEN_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR


class ClaimTokenStore:
    """Generate, verify, and retire the one-time instance-claim token."""

    def __init__(self, settings: "Settings"):
        self._settings = settings

    def _token_path(self) -> Optional[Path]:
        """On-disk location of the token file, or None if storage is undetermined."""
        try:
            storage_dir = self._settings.get_file_storage_directory()
        except Exception as exc:  # settings/DB not ready yet
            logger.warning(
                "Could not resolve the file storage directory for the setup "
                "claim token (%s); it cannot be generated yet.", exc
            )
            return None
        if not storage_dir:
            return None
        return Path(storage_dir) / CLAIM_TOKEN_FILENAME

    def exists(self) -> bool:
        """True when a setup token is currently on disk."""
        path = self._token_path()
        return bool(path and path.exists())

    def ensure_token(self) -> Optional[str]:
        """Return the current token, generating and persisting one if absent.

        Returns None only when the file storage directory cannot be resolved or
        written; callers treat that as "no token available".
        """
        path = self._token_path()
        if path is None:
            return None

        if path.exists():
            try:
                existing = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.warning(
                    "Could not read the setup claim token at %s (%s); "
                    "regenerating it.", path, exc
                )
                existing = ""
            if existing:
                return existing

        return self._generate(path)

    def verify(self, token: Optional[str]) -> bool:
        """Constant-time check of `token` against the persisted setup token."""
        if not token:
            return False
        path = self._token_path()
        if not path or not path.exists():
            return False
        try:
            current = path.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if not current:
            return False
        return secrets.compare_digest(current, token.strip())

    def clear(self) -> None:
        """Delete the token file once the instance is claimed. Best-effort."""
        path = self._token_path()
        if path and path.exists():
            try:
                path.unlink()
            except OSError as exc:
                logger.warning(
                    "Could not remove the consumed setup claim token at %s "
                    "(%s); delete it manually.", path, exc
                )

    def _generate(self, path: Path) -> Optional[str]:
        """Create a fresh token and atomically persist it with 0600 perms."""
        token = secrets.token_urlsafe(32)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{CLAIM_TOKEN_FILENAME}.", dir=str(path.parent)
            )
            try:
                os.fchmod(fd, _TOKEN_FILE_MODE)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(token)
            except BaseException:
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            os.replace(tmp_name, path)
            try:
                os.chmod(path, _TOKEN_FILE_MODE)
            except OSError:
                pass
            logger.info(
                "This instance has no owner yet; wrote a one-time setup token to "
                "%s. A claim from a non-local address must present it.", path
            )
            return token
        except OSError as exc:
            logger.warning(
                "Could not write the setup claim token to %s (%s); remote "
                "claiming cannot be gated by a token until this path is "
                "writable.", path, exc
            )
            try:
                if "tmp_name" in locals() and os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except OSError:
                pass
            return None
