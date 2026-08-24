"""Resolves the admin-configured storage backend into a `FileStorageDriver`.

Mirrors how `src/features/llm/repository.py` handles `llm_configurations.api_key`:
the core `settings` table has no per-row encryption of its own, so the one
field that needs it (`s3_secret_key`) is encrypted/decrypted explicitly at
this boundary, through the same `SecretCipher` used everywhere else.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.platform.security.secrets import get_secret_cipher
from src.platform.settings.settings import SettingsManager
from src.platform.filesystem.storage_driver import FileStorageDriver, LocalFileStorageDriver
from src.platform.filesystem.s3_driver import S3FileStorageDriver

logger = logging.getLogger(__name__)

BACKEND_LOCAL = "local"
BACKEND_S3 = "s3"


@dataclass(frozen=True)
class S3StorageConfig:
    bucket: str
    access_key_id: str
    secret_key: str
    region: str
    endpoint_url: Optional[str]
    prefix: str
    path_style: bool


class StorageSettingsManager:
    """Reads the `storage_backend`/`s3_*` settings and builds the driver they
    describe. New writes go through whatever `build_driver()` returns at the
    time; switching backends never touches files already written under the
    previous one."""

    def __init__(self, settings_manager: SettingsManager):
        self.settings = settings_manager

    def get_backend(self) -> str:
        backend = self.settings.get_setting("storage_backend", BACKEND_LOCAL)
        return backend if backend in (BACKEND_LOCAL, BACKEND_S3) else BACKEND_LOCAL

    def get_s3_config(self) -> S3StorageConfig:
        encrypted_secret = self.settings.get_setting("s3_secret_key", "")
        secret = ""
        if encrypted_secret:
            secret = get_secret_cipher().decrypt_if_encrypted(
                encrypted_secret, context="settings:s3_secret_key"
            )
        return S3StorageConfig(
            bucket=self.settings.get_setting("s3_bucket", ""),
            access_key_id=self.settings.get_setting("s3_access_key_id", ""),
            secret_key=secret,
            region=self.settings.get_setting("s3_region", "us-east-1"),
            endpoint_url=self.settings.get_setting("s3_endpoint_url", "") or None,
            prefix=self.settings.get_setting("s3_prefix", ""),
            path_style=bool(self.settings.get_setting("s3_path_style", False)),
        )

    def set_s3_secret_key(self, plaintext: str) -> bool:
        """Encrypts and stores the S3 secret access key. Callers must not
        write `s3_secret_key` through the generic settings endpoint with a
        plaintext value - the generic path never encrypts this field."""
        if not plaintext:
            return self.settings.set_setting("s3_secret_key", "")
        return self.settings.set_setting("s3_secret_key", get_secret_cipher().encrypt(plaintext))

    def build_driver(self, local_base_dir: str) -> FileStorageDriver:
        """`local_base_dir` is `file_storage_directory` - always needed even
        when the backend is S3, since local staging (probing media metadata
        with ffprobe/PIL, which need a real file) never goes away.

        Falls back to the local driver, with a logged error, when
        `storage_backend` is `'s3'` but incompletely configured - a bad S3
        setting must degrade the storage backend, not take the whole process
        down at boot.
        """
        self._warn_if_configured_directory_looks_wrong(local_base_dir)
        if self.get_backend() != BACKEND_S3:
            return LocalFileStorageDriver(local_base_dir)

        config = self.get_s3_config()
        if not config.bucket or not config.access_key_id or not config.secret_key:
            logger.error(
                "storage_backend is 's3' but s3_bucket/s3_access_key_id/s3_secret_key "
                "are not fully configured - falling back to local storage."
            )
            return LocalFileStorageDriver(local_base_dir)

        return S3FileStorageDriver(
            bucket=config.bucket,
            access_key_id=config.access_key_id,
            secret_access_key=config.secret_key,
            region=config.region,
            endpoint_url=config.endpoint_url,
            prefix=config.prefix,
            path_style=config.path_style,
        )

    @staticmethod
    def _warn_if_configured_directory_looks_wrong(local_base_dir: str) -> None:
        """`LocalFileStorageDriver` creates `local_base_dir` on first use if it
        doesn't exist yet - silently, since a fresh install has nothing there.
        But on an EXISTING install, every `files`/`uploads` row points at a key
        under whatever directory was configured when it was written; if the
        setting now names a directory that doesn't exist while the default
        `./storage` does, every one of those reads is about to 404 against an
        empty directory that was just silently created (the failure mode a
        2026-08-15 test-isolation bug caused by writing a throwaway tmp_path
        into this exact setting - see `preset_suite.repository`). Warn loudly;
        never rewrite the setting - a genuinely fresh install pointed at a new
        directory on purpose looks identical from here.
        """
        configured = Path(local_base_dir)
        default = Path("storage")
        if configured.resolve() == default.resolve():
            return
        if not configured.exists() and default.is_dir():
            logger.warning(
                "file_storage_directory is set to %r, which does not exist yet, "
                "while the default './storage' directory does. Every existing "
                "generation/upload will 404 until this is corrected - check "
                "Admin -> Settings -> file_storage_directory.",
                local_base_dir,
            )
