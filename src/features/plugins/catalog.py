import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

CATALOG_SCHEMA = 1

_PLUGIN_ID = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9_-])?")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_WINDOWS_DEVICES = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"{name}{digit}" for name in ("com", "lpt") for digit in "0123456789¹²³"]
)


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    name: str
    version: str
    description: str
    license: str
    requires_core: str
    archive: Optional[str]
    sha256: Optional[str]


@dataclass(frozen=True)
class CatalogProblem:
    index: int
    plugin_id: Optional[str]
    reason: str


def is_installable_plugin_id(value: str) -> bool:
    return bool(_PLUGIN_ID.fullmatch(value)) and value.split(".", 1)[0] not in _WINDOWS_DEVICES


def _text(raw: dict, key: str, default: Optional[str] = None) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{key} is not a non-empty string")
    return value.strip()


def _optional_text(raw: dict, key: str) -> str:
    value = raw.get(key)
    return value.strip() if isinstance(value, str) else ""


def _release(raw: Any) -> Tuple[Optional[str], Optional[str]]:
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        raise CatalogError("release is not an object")
    archive, sha256 = raw.get("archive"), raw.get("sha256")
    if archive is None and sha256 is None:
        return None, None
    if not isinstance(archive, str) or not archive.strip():
        raise CatalogError("release archive is not a URL")
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise CatalogError("release sha256 is not 64 lowercase hex digits")
    return archive.strip(), sha256


def _entry(raw: Any) -> CatalogEntry:
    if not isinstance(raw, dict):
        raise CatalogError("the entry is not an object")
    plugin_id = _text(raw, "id")
    if not is_installable_plugin_id(plugin_id):
        raise CatalogError(f"id {plugin_id!r} is not a plugin folder name PotionUI installs")
    requires = raw.get("requires")
    if not isinstance(requires, dict):
        raise CatalogError("requires is not an object")
    archive, sha256 = _release(raw.get("release"))
    return CatalogEntry(
        id=plugin_id,
        name=_text(raw, "name", plugin_id),
        version=_text(raw, "version"),
        description=_optional_text(raw, "description"),
        license=_optional_text(raw, "license"),
        requires_core=_text(requires, "core"),
        archive=archive,
        sha256=sha256,
    )


def parse_catalog(data: bytes) -> Tuple[List[CatalogEntry], List[CatalogProblem]]:
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise CatalogError(f"The catalog is not JSON: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("plugins"), list):
        raise CatalogError("The catalog is not an object with a plugins list.")
    if document.get("schema") != CATALOG_SCHEMA:
        raise CatalogError(f"The catalog schema {document.get('schema')!r} is not {CATALOG_SCHEMA}.")

    parsed: List[Tuple[int, CatalogEntry]] = []
    problems: List[CatalogProblem] = []
    for index, raw in enumerate(document["plugins"]):
        try:
            parsed.append((index, _entry(raw)))
        except CatalogError as error:
            raw_id = raw.get("id") if isinstance(raw, dict) else None
            problems.append(CatalogProblem(index, raw_id if isinstance(raw_id, str) else None, str(error)))

    counts = Counter(entry.id for _, entry in parsed)
    entries: List[CatalogEntry] = []
    for index, entry in parsed:
        if counts[entry.id] > 1:
            problems.append(CatalogProblem(index, entry.id, "the catalog lists this id more than once"))
        else:
            entries.append(entry)
    return entries, problems


def core_mismatch(requires_core: str, core_version: str) -> Optional[str]:
    text = requires_core.strip()
    try:
        specifier = SpecifierSet(text) if text[:1] in "<>=!~" else SpecifierSet(f">={Version(text)}")
        satisfied = specifier.contains(Version(core_version), prereleases=True)
    except (InvalidSpecifier, InvalidVersion):
        return f"it requires core {requires_core!r}, which is not a version or a version range"
    if satisfied:
        return None
    return f"it needs PotionUI {specifier}, this is {core_version}"
