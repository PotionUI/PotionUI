"""Where the models live on disk, and how an admin relocates them.

The rest of the app reads models from the stable `<models_root>/<type_dir>/...`
layout (`src.platform.filesystem.model_types.MODEL_DIRECTORY_NAMES`) and always
will - no path resolver changes when the location changes. "Relocating the
models directory" means pointing each `<models_root>/<type_dir>` at an external
directory via a symlink, and swapping what that symlink targets. A per-type
override lets one type (e.g. `loras`) live somewhere different from the rest.
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from src.features.models.root_detection import ResolvedTarget, resolve_targets
from src.platform.filesystem.model_types import MODEL_DIRECTORY_NAMES
from src.platform.settings.records import Setting, SettingType, SettingValueType
from src.platform.settings.repository import SettingRepository

logger = logging.getLogger(__name__)

EXTERNAL_PATH_SETTING_KEY = "models_location_external_path"
OVERRIDES_SETTING_KEY = "models_location_overrides"


class ModelsLocationError(Exception):
    """Refused to apply a models location change. `.reason` is user-facing."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ModelsRelocator:
    """Owns the external models location and the `<models_root>/<type>` symlinks pointing at it.

    `generation_active` is injected as a zero-arg callable rather than a queue
    object so this class stays ignorant of the generation feature's shape - it
    only needs a yes/no answer to "is anything running or queued right now".
    """

    def __init__(
        self,
        models_root: Path,
        setting_repository: SettingRepository,
        generation_active: Optional[callable] = None,
    ):
        self.models_root = Path(models_root)
        self.settings = setting_repository
        self._generation_active = generation_active or (lambda: False)

    # ---------------------------------------------------------------- config

    def get_config(self) -> Dict[str, Any]:
        external_path = self._get_setting(EXTERNAL_PATH_SETTING_KEY, None)
        overrides = self._get_setting(OVERRIDES_SETTING_KEY, {}) or {}
        resolved = resolve_targets(external_path, overrides) if external_path else {}

        directories = [
            self._directory_status(name, external_path, overrides, resolved.get(name))
            for name in MODEL_DIRECTORY_NAMES
        ]

        return {
            "external_path": external_path,
            "overrides": overrides,
            "directories": directories,
            "windows_unsupported": self._is_windows(),
        }

    def _directory_status(
        self,
        name: str,
        external_path: Optional[str],
        overrides: Dict[str, str],
        resolved_entry: Optional[ResolvedTarget],
    ) -> Dict[str, Any]:
        target = None
        auto_matched = False
        if resolved_entry is not None:
            target = str(resolved_entry.path)
            if name not in overrides:
                naive = self._joined(external_path, name)
                auto_matched = resolved_entry.matched and target != naive

        link_path = self.models_root / name
        is_symlink = link_path.is_symlink()
        return {
            "directory": name,
            "target": target,
            "linked": is_symlink,
            "resolved_target": str(link_path.resolve()) if is_symlink else None,
            "has_real_files": self._has_real_files(link_path),
            "auto_matched": auto_matched,
        }

    # --------------------------------------------------------------- applying

    def apply(self, external_path: str, overrides: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Validate, then point every type directory's symlink at its target.

        Raises `ModelsLocationError` (never partially applies) if any of the
        guardrails in `_validate` trip.
        """
        overrides = {k: v for k, v in (overrides or {}).items() if v}
        self._validate(external_path, overrides)

        resolved = resolve_targets(external_path, overrides)

        auto_matched = []
        created_empty = []
        for name, entry in resolved.items():
            self._reconcile_one(name, entry.path)
            if name in overrides:
                continue
            naive = Path(self._joined(external_path, name))
            if entry.matched:
                if entry.path != naive:
                    auto_matched.append(name)
            else:
                created_empty.append(name)

        self._upsert_setting(EXTERNAL_PATH_SETTING_KEY, external_path, SettingValueType.STRING)
        self._upsert_setting(OVERRIDES_SETTING_KEY, overrides, SettingValueType.JSON)

        logger.info(
            f"Models location applied: external_path={external_path} overrides={overrides} "
            f"auto_matched={auto_matched} created_empty={created_empty}"
        )
        result = self.get_config()
        result["auto_matched"] = auto_matched
        result["created_empty"] = created_empty
        return result

    def _validate(self, external_path: str, overrides: Dict[str, str]) -> None:
        if self._is_windows():
            raise ModelsLocationError(
                "Relocating the models directory isn't supported on Windows yet - "
                "creating a symlink there needs elevated privileges. Move the files "
                "manually and point the individual directories at them instead."
            )

        if self._generation_active():
            raise ModelsLocationError(
                "A generation is currently running or queued. Wait for it to finish "
                "before changing the models location."
            )

        if not external_path or not external_path.strip():
            raise ModelsLocationError("An external models directory path is required.")

        resolved = resolve_targets(external_path, overrides)
        conflicts = []
        for name, entry in resolved.items():
            type_dir = self.models_root / name
            if self._has_real_files(type_dir):
                conflicts.append((name, type_dir, entry.path))

        if conflicts:
            instructions = "; ".join(
                f"move the contents of '{type_dir}' into '{target}', then remove '{type_dir}'"
                for _, type_dir, target in conflicts
            )
            names = ", ".join(name for name, _, _ in conflicts)
            raise ModelsLocationError(
                f"'{self.models_root}' already contains real files for: {names}. "
                f"PotionUI will not move them for you - {instructions}."
            )

    def _reconcile_one(self, name: str, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        self.models_root.mkdir(parents=True, exist_ok=True)

        link_path = self.models_root / name
        if link_path.is_symlink():
            link_path.unlink()
        elif link_path.exists():
            # _validate already refused if this held real files - an empty real
            # directory (or a stray empty file) is safe to replace.
            if link_path.is_dir():
                shutil.rmtree(link_path)
            else:
                link_path.unlink()

        link_path.symlink_to(target, target_is_directory=True)

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _is_windows() -> bool:
        return os.name == "nt"

    @staticmethod
    def _joined(base: str, name: str) -> str:
        return str(Path(base) / name)

    @staticmethod
    def _has_real_files(directory: Path) -> bool:
        """True if `directory` exists, is not itself a symlink, and holds any file."""
        if directory.is_symlink() or not directory.exists():
            return False
        if not directory.is_dir():
            return True  # a real *file* sitting where a type directory belongs
        return any(p.is_file() for p in directory.rglob("*"))

    def _get_setting(self, key: str, default: Any) -> Any:
        setting = self.settings.get_setting_by_key(key)
        return setting.get_typed_value() if setting else default

    def _upsert_setting(self, key: str, value: Any, value_type: SettingValueType) -> None:
        serialized = Setting.serialize_value(value, value_type)
        setting = self.settings.get_setting_by_key(key)
        if setting:
            self.settings.update_setting_value(setting.id, serialized)
        else:
            self.settings.create_setting(key, serialized, value_type, setting_type=SettingType.SYSTEM)
