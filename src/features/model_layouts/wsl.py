from __future__ import annotations

import ntpath
import os
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from src.platform.filesystem.model_roots import is_windows as host_is_windows

_DRIVE_RE = re.compile(r"^([A-Za-z]):(?:[\\/](.*))?$")
_ENV_RE = re.compile(r"\$[{(A-Za-z_0-9@*#?!$]|`|%[^%\s]+%|%~[A-Za-z0-9]+|%[0-9]")


@dataclass(frozen=True)
class Translation:
    path: Optional[str]
    warning: Optional[str] = None


@dataclass(frozen=True)
class PathTranslator:
    is_windows: bool = field(default_factory=host_is_windows)
    mount_root: str = "/mnt"
    is_dir: Callable[[str], bool] = os.path.isdir
    expand_user: Callable[[str], str] = os.path.expanduser

    @property
    def flavor(self) -> Any:
        return ntpath if self.is_windows else posixpath

    def translate(self, raw: str) -> Translation:
        value = raw.strip()
        if not value:
            return Translation(None)
        if _ENV_RE.search(value):
            return Translation(None, f"'{raw}' uses an environment variable, which cannot be resolved here")
        if value == "~" or value.startswith(("~/", "~\\")):
            value = self.expand_user(value)
        if self.is_windows:
            if value.startswith("/") and not value.startswith("//"):
                return Translation(None, f"'{raw}' is a POSIX path, which does not exist on Windows")
            return Translation(value)
        drive = _DRIVE_RE.match(value)
        if drive:
            letter = drive.group(1).lower()
            mount = f"{self.mount_root.rstrip('/')}/{letter}"
            if not self.is_dir(mount):
                return Translation(None, f"'{raw}' is a Windows path but {mount} is not mounted")
            rest = (drive.group(2) or "").replace("\\", "/").strip("/")
            return Translation(f"{mount}/{rest}" if rest else mount)
        if value.startswith("\\\\"):
            return Translation(None, f"'{raw}' is a Windows network path, which cannot be reached from here")
        if "\\" in value and "/" not in value:
            value = value.replace("\\", "/")
        return Translation(value)


def default_translator() -> PathTranslator:
    return PathTranslator()
