"""
Authentication configuration loaded from settings/environment.
"""
import os
import secrets
import logging
import stat
import tempfile
from pathlib import Path
from typing import Optional, TYPE_CHECKING


if TYPE_CHECKING:
    # Injected, never constructed here. Importing it for real would reach
    # persistence, which maps rows onto the `User` in this package - a loop.
    from src.platform.settings.settings import SettingsManager

logger = logging.getLogger(__name__)

# The generated JWT secret is persisted here, under the file storage directory,
# so sessions survive a restart on installs that never set a secret explicitly.
SECRET_KEY_FILENAME = "auth_secret.key"
# Owner read/write only (0600). A secret readable by other users is a leak.
_SECRET_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR


def warn_if_insecure_permissions(path: Path, what: str) -> None:
    """Warn when a secret file at `path` is group/world accessible, but keep
    using it - refusing to start would be worse than a warned-about permission.
    `what` names the secret in the log message (e.g. "auth secret file")."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        logger.warning(
            "The %s at %s is accessible to other users (mode %o); tighten "
            "it to 0600. Continuing to use it.",
            what, path, mode
        )


class AuthConfig:
    """
    Authentication configuration loaded from settings/environment.

    Configuration priority:
    1. Environment variables (POTIONUI_AUTH_*)
    2. Database settings via SettingsManager
    3. Default values
    """

    def __init__(self, settings_manager: "SettingsManager"):
        self._settings = settings_manager
        self._cached_secret_key: Optional[str] = None

    @property
    def secret_key(self) -> str:
        """
        JWT secret key.

        Resolution order (first match wins):

        1. Environment variable ``POTIONUI_AUTH_SECRET_KEY``.
        2. The ``auth_secret_key`` database setting (an admin-configured value).
        3. A persisted key file under the file storage directory. On the very
           first boot with no configured secret this file does not exist yet, so
           we generate a new secret and write it there (0600) - every later
           restart then reads it back, keeping sessions valid.

        If none of the above yields a key and the file cannot be written, we
        fall back to an in-memory random key and warn loudly: sessions will not
        survive a restart until the operator sets one of the durable sources.
        """
        if self._cached_secret_key is not None:
            return self._cached_secret_key

        # 1. Environment variable - highest priority, never persisted by us.
        env_key = os.environ.get("POTIONUI_AUTH_SECRET_KEY")
        if env_key:
            self._cached_secret_key = env_key
            return self._cached_secret_key

        # 2. Database setting - an operator may have set this explicitly.
        settings_key = self._settings.get_setting("auth_secret_key", None)
        if settings_key:
            self._cached_secret_key = settings_key
            return self._cached_secret_key

        # 3. Persisted key file (durable across restarts without any config).
        self._cached_secret_key = self._load_or_create_persisted_secret()
        return self._cached_secret_key

    def _secret_key_path(self) -> Optional[Path]:
        """Resolve the on-disk location of the persisted secret, or None if the
        file storage directory cannot be determined."""
        try:
            storage_dir = self._settings.get_file_storage_directory()
        except Exception as exc:  # settings/DB not ready - fall back to in-memory
            logger.warning(
                "Could not resolve the file storage directory for the auth "
                "secret (%s); a persistent secret cannot be stored yet.", exc
            )
            return None
        if not storage_dir:
            return None
        return Path(storage_dir) / SECRET_KEY_FILENAME

    def _load_or_create_persisted_secret(self) -> str:
        """Read the secret from its key file, or generate and persist one.

        Never logs the secret value itself. Warns (but still uses the value) when
        the existing file has looser-than-0600 permissions, and falls back to an
        in-memory key when the file cannot be written.
        """
        path = self._secret_key_path()
        if path is None:
            return self._ephemeral_secret(reason="no file storage directory")

        # Read an existing key if present.
        if path.exists():
            try:
                existing = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.warning(
                    "Could not read the auth secret file at %s (%s); using a "
                    "temporary in-memory key. Sessions will reset on restart "
                    "until this file is readable.", path, exc
                )
                return self._ephemeral_secret(reason="unreadable secret file")
            if existing:
                warn_if_insecure_permissions(path, "auth secret file")
                return existing
            # Empty/corrupt file - fall through and regenerate it.
            logger.warning(
                "The auth secret file at %s was empty; regenerating it.", path
            )

        # Generate and persist a fresh secret.
        new_secret = secrets.token_urlsafe(32)
        if self._persist_secret(path, new_secret):
            logger.info(
                "Generated a new persistent auth secret at %s. Keep this file "
                "safe; deleting it invalidates all existing sessions.", path
            )
            return new_secret

        return self._ephemeral_secret(reason=f"could not write {path}")

    def _persist_secret(self, path: Path, secret: str) -> bool:
        """Atomically write `secret` to `path` with 0600 permissions.

        Returns True on success. Uses a same-directory temp file plus an atomic
        rename so a concurrent first boot can never observe a half-written file;
        the last writer wins and both processes end up with a valid key file.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{SECRET_KEY_FILENAME}.", dir=str(path.parent)
            )
            try:
                os.fchmod(fd, _SECRET_FILE_MODE)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(secret)
            except BaseException:
                # fdopen consumes fd; guard the pre-fdopen window too.
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            os.replace(tmp_name, path)
            # Re-assert mode in case a restrictive umask altered the final file.
            try:
                os.chmod(path, _SECRET_FILE_MODE)
            except OSError:
                pass
            return True
        except OSError as exc:
            logger.warning(
                "Could not write the persistent auth secret to %s (%s); using a "
                "temporary in-memory key instead. Sessions will reset on every "
                "restart until this path is writable or "
                "POTIONUI_AUTH_SECRET_KEY is set.", path, exc
            )
            # Best-effort cleanup of a stray temp file.
            try:
                if 'tmp_name' in locals() and os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except OSError:
                pass
            return False

    def _ephemeral_secret(self, reason: str) -> str:
        """Generate a non-persistent secret and warn that sessions won't survive
        a restart. The reason names why persistence was unavailable."""
        logger.warning(
            "No persistent auth secret is available (%s); using a random "
            "in-memory key. All sessions will be invalidated on the next "
            "restart. Set POTIONUI_AUTH_SECRET_KEY or make the file storage "
            "directory writable to fix this.", reason
        )
        return secrets.token_urlsafe(32)

    @property
    def algorithm(self) -> str:
        """JWT algorithm. Default: HS256"""
        return os.environ.get("POTIONUI_AUTH_ALGORITHM", "HS256")

    @property
    def access_token_expire_minutes(self) -> int:
        """Token expiration in minutes. Default: 1440 (24 hours)"""
        env_minutes = os.environ.get("POTIONUI_AUTH_TOKEN_EXPIRE_MINUTES")
        if env_minutes:
            try:
                return int(env_minutes)
            except ValueError:
                logger.warning(f"Invalid POTIONUI_AUTH_TOKEN_EXPIRE_MINUTES: {env_minutes}")

        settings_minutes = self._settings.get_setting("auth_token_expire_minutes", None)
        if settings_minutes is not None:
            try:
                return int(settings_minutes)
            except (ValueError, TypeError):
                logger.warning(f"Invalid auth_token_expire_minutes setting: {settings_minutes}")

        return 1440  # 24 hours default

    @property
    def remember_me_token_expire_days(self) -> int:
        """Remember me token expiration in days. Default: 30"""
        env_days = os.environ.get("POTIONUI_AUTH_REMEMBER_ME_EXPIRE_DAYS")
        if env_days:
            try:
                return int(env_days)
            except ValueError:
                logger.warning(f"Invalid POTIONUI_AUTH_REMEMBER_ME_EXPIRE_DAYS: {env_days}")

        settings_days = self._settings.get_setting("auth_remember_me_expire_days", None)
        if settings_days is not None:
            try:
                return int(settings_days)
            except (ValueError, TypeError):
                logger.warning(f"Invalid auth_remember_me_expire_days setting: {settings_days}")

        return 30  # 30 days default
