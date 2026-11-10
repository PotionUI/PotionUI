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
from src.features.plugins.catalog import INSTALL_RECORD, CatalogEntry, CatalogProblem, core_mismatch, parse_catalog
from src.features.plugins.operations.lifecycle import disable_plugin, enable_plugin, rescan_presets_and_pipes
from src.features.plugins.operations.scan import scan_plugins
from src.features.plugins.remote import fetch_bytes, local_sources_allowed
from src.features.plugins.repository import PluginRepository
from src.platform.filesystem.yaml_io import load_yaml
from src.platform.http.targets import origin_of
from src.platform.plugins.manifest import PluginManifestSchema
from src.platform.plugins.registry import PluginRegistry, PluginState
from src.platform.version import POTIONUI_VERSION

logger = logging.getLogger(__name__)

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


def _catalog_host(recorded: Any) -> str:
    if not isinstance(recorded, str):
        return "unknown"
    return urlsplit(recorded.strip()).hostname or "unknown"


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
            entry, "conflict", f"A plugin with this id is already installed in the folder {Path(manifest.plugin_dir).name}.", manifest.version
        )

    package = entry.id.replace("-", "_")
    for other in registry.get_all_plugins():
        if other.id != entry.id and other.id.replace("-", "_") == package:
            return CatalogItem(
                entry,
                "conflict",
                f"The installed plugin {other.id} uses the same Python package name, {package}.",
                manifest.version if manifest is not None else None,
            )

    installed_version = None
    if folder.exists():
        record = _install_record(folder)
        if record is None:
            return CatalogItem(
                entry,
                "conflict",
                f"The folder {folder.name} was not installed from a catalog; remove it to install this plugin from here.",
                manifest.version if manifest is not None else None,
            )
        installed_version = manifest.version if manifest is not None else str(record.get("version") or "")
        if not _same_catalog(record.get("catalog"), catalog_url):
            return CatalogItem(
                entry,
                "conflict",
                f"This plugin was installed from another catalog ({_catalog_host(record.get('catalog'))}); "
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


@dataclass(frozen=True)
class _Staged:
    manifest: PluginManifestSchema
    staging: Path
    unpacked: Path


@dataclass(frozen=True)
class _Placement:
    manifest: PluginManifestSchema
    staging: Path
    previous: Optional[Path]


def _discard_staging(staging: Path) -> None:
    shutil.rmtree(staging, ignore_errors=True)
    try:
        staging.parent.rmdir()
    except OSError:
        pass


def _stage(data: bytes, item: CatalogItem, local_dir: Path, catalog_url: str) -> _Staged:
    entry = item.entry
    staging_root = local_dir / STAGING_FOLDER
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=staging_root))
    try:
        unpacked = unpack_plugin_archive(data, entry.id, staging / "new")
        manifest = _checked_manifest(unpacked, entry)
        record = {"catalog": catalog_url, "id": entry.id, "version": entry.version, "archive": entry.archive, "sha256": entry.sha256}
        (unpacked / INSTALL_RECORD).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return _Staged(manifest, staging, unpacked)
    except BaseException:
        _discard_staging(staging)
        raise


def _place(staged: _Staged, item: CatalogItem, local_dir: Path) -> _Placement:
    staging, unpacked = staged.staging, staged.unpacked
    target = local_dir / item.entry.id
    previous = None
    try:
        if item.status == "update":
            if _install_record(target) is None:
                raise CatalogInstallError(f"The folder {target.name} changed since the catalog was read; nothing was replaced.")
            previous = staging / "previous"
            os.replace(target, previous)
            try:
                os.replace(unpacked, target)
            except OSError as error:
                try:
                    os.replace(previous, target)
                except OSError as second:
                    raise CatalogInstallError(
                        f"The folder {target.name} could not be replaced ({error}) nor put back ({second}); "
                        f"the installed {item.installed_version} is kept in {STAGING_FOLDER}/{staging.name}/previous."
                    ) from second
                raise
        else:
            if target.exists():
                raise CatalogInstallError(f"The folder {target.name} appeared since the catalog was read; nothing was replaced.")
            os.replace(unpacked, target)
        return _Placement(staged.manifest, staging, previous)
    except BaseException:
        if previous is None or not previous.exists():
            _discard_staging(staging)
        raise


def _put_previous_back(placement: _Placement, target: Path) -> None:
    failed = placement.staging / "failed"
    os.replace(target, failed)
    try:
        os.replace(placement.previous, target)
    except OSError:
        os.replace(failed, target)
        raise


def _disable(repo: PluginRepository, registry: PluginRegistry, plugin_id: str, collaborators: dict) -> None:
    if registry.get_plugin_state(plugin_id) == PluginState.ENABLED:
        disable_plugin(repo, registry, plugin_id, **collaborators)
    else:
        repo.disable_plugin(plugin_id)


def _came_back(was_enabled: bool, state: Optional[PluginState]) -> bool:
    if state is None:
        return False
    if was_enabled:
        return state == PluginState.ENABLED
    return state != PluginState.ERROR


def _not_picked_up(registry: PluginRegistry, item: CatalogItem, target: Path) -> Optional[str]:
    manifest = registry.get_plugin(item.entry.id)
    if manifest is None:
        return "the scan did not find its manifest"
    if manifest.version != item.entry.version or not _same_folder(manifest.plugin_dir, target):
        return "the scan did not pick up its manifest"
    return None


def _left_in_place(item: CatalogItem, target: Path, reason: str) -> CatalogInstallError:
    return CatalogInstallError(
        f"{item.entry.id} {item.entry.version} is in the folder {target.name}, but {reason}; it stays there, disabled. "
        "Remove that folder to install it again."
    )


def _same_version(first: Optional[str], second: Optional[str]) -> bool:
    if first == second:
        return True
    try:
        return Version(str(first)) == Version(str(second))
    except InvalidVersion:
        return False


def _row_enabled(repo: PluginRepository, plugin_id: str) -> bool:
    row = repo.get_plugin_by_id(plugin_id)
    return row is not None and bool(row.enabled)


async def _restore_previous(
    repo: PluginRepository,
    registry: PluginRegistry,
    item: CatalogItem,
    placement: _Placement,
    target: Path,
    was_enabled: bool,
    collaborators: dict,
    failure: str,
) -> None:
    plugin_id = item.entry.id
    try:
        await asyncio.to_thread(_put_previous_back, placement, target)
    except OSError as error:
        _disable(repo, registry, plugin_id, collaborators)
        raise CatalogInstallError(
            f"{plugin_id} {item.entry.version} did not start ({failure}), and the installed "
            f"{item.installed_version} could not be put back ({error}); it is kept in "
            f"{STAGING_FOLDER}/{placement.staging.name}/previous, and {plugin_id} is disabled."
        ) from error
    message = (
        f"{plugin_id} {item.entry.version} did not start ({failure}); "
        f"the installed {item.installed_version} is back in place"
    )
    try:
        scan_plugins(repo, registry)
    except Exception as error:
        _disable(repo, registry, plugin_id, collaborators)
        raise CatalogInstallError(
            f"{message}, but the scan that followed failed ({type(error).__name__}: {error}), so {plugin_id} is disabled."
        ) from error
    restored = registry.get_plugin(plugin_id)
    if restored is None or not _same_version(restored.version, item.installed_version) or not _same_folder(
        restored.plugin_dir, target
    ):
        _disable(repo, registry, plugin_id, collaborators)
        raise CatalogInstallError(f"{message}, but the scan did not pick it up, so {plugin_id} is disabled.")
    if was_enabled:
        if registry.get_plugin_state(plugin_id) == PluginState.ENABLED:
            repo.enable_plugin(plugin_id)
            rescan_presets_and_pipes(
                collaborators["preset_loader"],
                collaborators["pipe_catalog"],
                collaborators["recipe_catalog"],
                f"restore of plugin {plugin_id}",
                collaborators["model_layout_catalog"],
            )
        else:
            try:
                enable_plugin(repo, registry, plugin_id, **collaborators)
            except Exception as error:
                raise CatalogInstallError(f"{message}, but did not start again either: {error}") from error
        message += f" and running. Restart PotionUI to unload any module {item.entry.version} imported"
    logger.warning(message)
    raise CatalogInstallError(f"{message}.")


async def _outlast(work: asyncio.Future) -> None:
    while not work.done():
        try:
            await asyncio.wait([work])
        except asyncio.CancelledError:
            continue
    if not work.cancelled() and work.exception() is not None:
        logger.warning(f"A catalog install whose request was cancelled ended with: {work.exception()}")


async def _install_downloaded(
    repo: PluginRepository,
    registry: PluginRegistry,
    url: str,
    item: CatalogItem,
    data: bytes,
    collaborators: dict,
) -> InstallResult:
    plugin_id = item.entry.id
    local_dir = local_plugin_dir(registry)
    staged = await asyncio.to_thread(_stage, data, item, local_dir, url)
    try:
        was_enabled = registry.get_plugin_state(plugin_id) == PluginState.ENABLED
        row_was_enabled = _row_enabled(repo, plugin_id)
        if row_was_enabled:
            repo.disable_plugin(plugin_id)
        took_down_an_earlier_copy = item.status == "available" and was_enabled
        if took_down_an_earlier_copy:
            _disable(repo, registry, plugin_id, collaborators)
    except BaseException:
        await asyncio.to_thread(_discard_staging, staged.staging)
        raise
    try:
        placement = await asyncio.to_thread(_place, staged, item, local_dir)
    except Exception as error:
        if item.status == "update" and not (local_dir / plugin_id).exists():
            _disable(repo, registry, plugin_id, collaborators)
            raise CatalogInstallError(f"{error} The plugin {plugin_id} is disabled.") from error
        if row_was_enabled and not took_down_an_earlier_copy:
            repo.enable_plugin(plugin_id)
        raise
    except BaseException:
        _disable(repo, registry, plugin_id, collaborators)
        raise
    target = local_dir / plugin_id
    try:
        try:
            scan_plugins(repo, registry)
        except Exception as error:
            if placement.previous is None:
                raise _left_in_place(item, target, f"its scan failed ({type(error).__name__}: {error})") from error
            failure = f"the scan failed: {type(error).__name__}: {error}"
            await _restore_previous(repo, registry, item, placement, target, was_enabled, collaborators, failure)
        missing = _not_picked_up(registry, item, target)
        state = registry.get_plugin_state(plugin_id)
        if placement.previous is not None and (missing or not _came_back(was_enabled, state)):
            failure = missing or registry.get_plugin_error(plugin_id) or "it did not start"
            await _restore_previous(repo, registry, item, placement, target, was_enabled, collaborators, failure)
        if missing:
            raise _left_in_place(item, target, missing)
        if row_was_enabled and item.status == "update" and state == PluginState.ENABLED:
            repo.enable_plugin(plugin_id)
    except BaseException:
        if registry.get_plugin_state(plugin_id) == PluginState.ENABLED and not _row_enabled(repo, plugin_id):
            _disable(repo, registry, plugin_id, collaborators)
        if placement.previous is None or not placement.previous.exists():
            await asyncio.to_thread(_discard_staging, placement.staging)
        raise
    await asyncio.to_thread(_discard_staging, placement.staging)
    if row_was_enabled and item.status == "available":
        logger.info(f"Plugin {plugin_id} was recorded as enabled from an earlier copy; the catalog install leaves it disabled")
    manifest = placement.manifest
    enabled = registry.get_plugin_state(plugin_id) == PluginState.ENABLED
    if enabled:
        rescan_presets_and_pipes(
            collaborators["preset_loader"],
            collaborators["pipe_catalog"],
            collaborators["recipe_catalog"],
            f"catalog update of plugin {plugin_id}",
            collaborators["model_layout_catalog"],
        )
    logger.info(f"Installed plugin {plugin_id} {item.entry.version} from a catalog on {urlsplit(url).hostname}")
    return InstallResult(
        plugin_id=plugin_id,
        version=item.entry.version,
        previous_version=item.installed_version,
        enabled=enabled,
        dependencies_python=list(manifest.dependencies.python),
    )


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

        collaborators = dict(
            preset_loader=preset_loader,
            pipe_catalog=pipe_catalog,
            recipe_catalog=recipe_catalog,
            model_layout_catalog=model_layout_catalog,
        )
        work = asyncio.ensure_future(_install_downloaded(repo, registry, url, item, data, collaborators))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            await _outlast(work)
            raise
