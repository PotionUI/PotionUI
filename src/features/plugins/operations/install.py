import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import urlsplit

from packaging.version import InvalidVersion, Version

from src.features.plugins.archive import unpack_plugin_archive
from src.features.plugins.catalog import CatalogEntry, CatalogProblem, core_mismatch, parse_catalog
from src.features.plugins.operations.lifecycle import _rescan_presets_and_pipes
from src.features.plugins.operations.scan import scan_plugins
from src.features.plugins.remote import fetch_bytes, local_sources_allowed
from src.features.plugins.repository import PluginRepository
from src.platform.filesystem.yaml_io import load_yaml
from src.platform.http.targets import origin_of
from src.platform.plugins.manifest import PluginManifestSchema
from src.platform.plugins.registry import PluginRegistry, PluginState
from src.platform.version import POTIONUI_VERSION

logger = logging.getLogger(__name__)

INSTALL_RECORD = ".potionui-catalog.json"
STAGING_FOLDER = ".potionui-install"
MAX_CATALOG_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
CATALOG_TIMEOUT_S = 30.0
ARCHIVE_TIMEOUT_S = 300.0

INSTALLABLE = ("available", "update")

_install_lock = asyncio.Lock()


class CatalogInstallError(ValueError):
    pass


@dataclass(frozen=True)
class CatalogItem:
    entry: CatalogEntry
    status: str
    reason: str = ""
    installed_version: Optional[str] = None


@dataclass(frozen=True)
class CatalogListing:
    url: str
    items: List[CatalogItem]
    problems: List[CatalogProblem]


@dataclass(frozen=True)
class InstallResult:
    plugin_id: str
    version: str
    previous_version: Optional[str]
    enabled: bool
    dependencies_python: List[str] = field(default_factory=list)


def _install_record(folder: Path) -> Optional[dict]:
    try:
        record = json.loads((folder / INSTALL_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _same_folder(first: Any, second: Path) -> bool:
    try:
        return Path(first).resolve() == second.resolve()
    except (OSError, TypeError):
        return False


def _same_catalog(recorded: Any, url: str) -> bool:
    if not isinstance(recorded, str) or not recorded.strip():
        return False
    first, second = recorded.strip(), url.strip()
    first_parts, second_parts = urlsplit(first), urlsplit(second)
    try:
        same_origin = origin_of(first) == origin_of(second)
    except ValueError:
        return False
    return same_origin and (first_parts.path, first_parts.query) == (second_parts.path, second_parts.query)


def _installed_item(entry: CatalogEntry, installed_version: str) -> Optional[CatalogItem]:
    if installed_version == entry.version:
        return CatalogItem(entry, "installed", "", installed_version)
    try:
        listed, current = Version(entry.version), Version(installed_version)
    except InvalidVersion:
        return CatalogItem(
            entry,
            "unreadable",
            f"PotionUI cannot order the listed {entry.version!r} and the installed {installed_version!r} as versions.",
            installed_version,
        )
    if listed == current:
        return CatalogItem(entry, "installed", "", installed_version)
    if listed < current:
        return CatalogItem(
            entry, "older", f"The catalog lists {entry.version}, older than the installed {installed_version}.", installed_version
        )
    return None


def catalog_item(
    entry: CatalogEntry,
    registry: PluginRegistry,
    local_dir: Path,
    catalog_url: str,
    core_version: str = POTIONUI_VERSION,
) -> CatalogItem:
    folder = local_dir / entry.id
    manifest = registry.get_plugin(entry.id)
    if manifest is not None and manifest.source == "marketplace":
        return CatalogItem(entry, "bundled", "PotionUI ships this plugin; a catalog copy would hide it.", manifest.version)
    if manifest is not None and not _same_folder(manifest.plugin_dir, folder):
        return CatalogItem(
            entry, "conflict", f"A plugin with this id is already installed in {manifest.plugin_dir}.", manifest.version
        )

    installed_version = None
    if folder.exists():
        record = _install_record(folder)
        if record is None:
            return CatalogItem(
                entry,
                "conflict",
                f"{folder} was not installed from a catalog; remove it to install this plugin from here.",
                manifest.version if manifest is not None else None,
            )
        installed_version = manifest.version if manifest is not None else str(record.get("version") or "")
        if not _same_catalog(record.get("catalog"), catalog_url):
            return CatalogItem(
                entry,
                "conflict",
                f"This plugin was installed from another catalog ({record.get('catalog') or 'unknown'}); "
                "only that catalog updates it.",
                installed_version,
            )
        settled = _installed_item(entry, installed_version)
        if settled is not None:
            return settled

    if entry.archive is None:
        return CatalogItem(entry, "unreleased", "The catalog lists no release archive for this version.", installed_version)
    mismatch = core_mismatch(entry.requires_core, core_version)
    if mismatch:
        return CatalogItem(entry, "incompatible", f"This version cannot run here: {mismatch}.", installed_version)
    if installed_version is not None:
        return CatalogItem(entry, "update", "", installed_version)
    return CatalogItem(entry, "available", "", None)


def local_plugin_dir(registry: PluginRegistry) -> Path:
    return Path(registry.loader.local_dir)


async def read_catalog(registry: PluginRegistry, url: str) -> CatalogListing:
    data = await fetch_bytes(
        url, max_bytes=MAX_CATALOG_BYTES, timeout_s=CATALOG_TIMEOUT_S, allow_local=local_sources_allowed()
    )
    entries, problems = parse_catalog(data)
    local_dir = local_plugin_dir(registry)
    return CatalogListing(url, [catalog_item(entry, registry, local_dir, url) for entry in entries], problems)


def _checked_manifest(unpacked: Path, entry: CatalogEntry) -> PluginManifestSchema:
    try:
        manifest = PluginManifestSchema.model_validate(load_yaml((unpacked / "manifest.yml").read_bytes()))
    except Exception as error:
        raise CatalogInstallError(f"The manifest in the archive is not valid: {error}") from error
    if manifest.id != entry.id or manifest.version != entry.version:
        raise CatalogInstallError(
            f"The archive holds {manifest.id} {manifest.version}, the catalog lists {entry.id} {entry.version}."
        )
    return manifest


def _place(data: bytes, item: CatalogItem, local_dir: Path, catalog_url: str) -> PluginManifestSchema:
    entry = item.entry
    staging_root = local_dir / STAGING_FOLDER
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=staging_root))
    try:
        unpacked = unpack_plugin_archive(data, entry.id, staging / "new")
        manifest = _checked_manifest(unpacked, entry)
        record = {"catalog": catalog_url, "id": entry.id, "version": entry.version, "archive": entry.archive, "sha256": entry.sha256}
        (unpacked / INSTALL_RECORD).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        target = local_dir / entry.id
        if item.status == "update":
            if _install_record(target) is None:
                raise CatalogInstallError(f"{target} changed since the catalog was read; nothing was replaced.")
            previous = staging / "previous"
            os.replace(target, previous)
            try:
                os.replace(unpacked, target)
            except OSError:
                os.replace(previous, target)
                raise
        else:
            if target.exists():
                raise CatalogInstallError(f"{target} appeared since the catalog was read; nothing was replaced.")
            os.replace(unpacked, target)
        return manifest
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            staging_root.rmdir()
        except OSError:
            pass


async def install_from_catalog(
    repo: PluginRepository,
    registry: PluginRegistry,
    url: str,
    plugin_id: str,
    *,
    version: str,
    sha256: str,
    preset_loader: Optional[Any] = None,
    pipe_catalog: Optional[Any] = None,
    recipe_catalog: Optional[Any] = None,
    model_layout_catalog: Optional[Any] = None,
) -> InstallResult:
    async with _install_lock:
        listing = await read_catalog(registry, url)
        item = next((candidate for candidate in listing.items if candidate.entry.id == plugin_id), None)
        if item is None:
            raise CatalogInstallError(f"The catalog lists no installable plugin {plugin_id!r}.")
        if (item.entry.version, item.entry.sha256) != (version, sha256):
            raise CatalogInstallError(
                f"The catalog now lists {plugin_id} {item.entry.version} with the sha256 {item.entry.sha256}, "
                f"not the {version} with the sha256 {sha256} you confirmed; nothing was installed. Read the catalog again."
            )
        if item.status not in INSTALLABLE:
            raise CatalogInstallError(item.reason or f"{plugin_id} {item.entry.version} is already installed.")

        data = await fetch_bytes(
            item.entry.archive, max_bytes=MAX_ARCHIVE_BYTES, timeout_s=ARCHIVE_TIMEOUT_S, allow_local=local_sources_allowed()
        )
        digest = hashlib.sha256(data).hexdigest()
        if digest != item.entry.sha256:
            raise CatalogInstallError(
                f"The archive's sha256 is {digest}, the catalog lists {item.entry.sha256}; nothing was installed."
            )

        manifest = await asyncio.to_thread(_place, data, item, local_plugin_dir(registry), url)
        scan_plugins(repo, registry)
        enabled = registry.get_plugin_state(plugin_id) == PluginState.ENABLED
        if enabled:
            _rescan_presets_and_pipes(
                preset_loader, pipe_catalog, recipe_catalog, f"catalog update of plugin {plugin_id}", model_layout_catalog
            )
        logger.info(f"Installed plugin {plugin_id} {item.entry.version} from {url}")
        return InstallResult(
            plugin_id=plugin_id,
            version=item.entry.version,
            previous_version=item.installed_version,
            enabled=enabled,
            dependencies_python=list(manifest.dependencies.python),
        )
