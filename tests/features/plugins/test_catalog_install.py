import asyncio
import hashlib
import io
import json
import shutil
import sqlite3
import threading
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.features.plugins import operations
from src.features.plugins.catalog import CatalogEntry
from src.features.plugins.operations import install as install_ops
from src.features.plugins.operations.install import INSTALL_RECORD, STAGING_FOLDER, CatalogInstallError, CatalogItem, catalog_item
from src.features.plugins.remote import ALLOW_LOCAL_SOURCES_ENV, FetchError
from src.features.plugins.records import Plugin
from src.features.plugins.repository import PluginRepository
from src.platform.database.rows import now_utc
from src.platform.plugins.registry import PluginRegistry, PluginState


def _manifest(plugin_id: str, version: str, **extra) -> bytes:
    manifest = {
        "id": plugin_id,
        "name": plugin_id,
        "version": version,
        "description": "Installed from a catalog",
        "author": "Test",
        "type": "backend-only",
        "dependencies": {"python": ["numpy>=1.24"]},
    }
    manifest.update(extra)
    return yaml.safe_dump(manifest).encode()


def _archive(plugin_id: str, version: str, *, manifest: bytes = None, extra=()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(f"{plugin_id}/manifest.yml", manifest or _manifest(plugin_id, version))
        bundle.writestr(f"{plugin_id}/README.md", f"{plugin_id} {version}\n")
        for name, content in extra:
            bundle.writestr(name, content)
    return buffer.getvalue()


CATALOG_A = "https://a.example.com/plugins.json"
CATALOG_B = "https://b.example.com/plugins.json"


def _write_plugin(folder: Path, plugin_id: str, version: str, *, record: bool, catalog=CATALOG_A) -> None:
    folder.mkdir(parents=True)
    (folder / "manifest.yml").write_bytes(_manifest(plugin_id, version))
    if record:
        fields = {"id": plugin_id, "version": version}
        if catalog is not None:
            fields["catalog"] = catalog
        (folder / INSTALL_RECORD).write_text(json.dumps(fields))


class Catalog:
    def __init__(self):
        self.entries = []
        self.archives = {}
        self.server = None

    def url(self, path: str = "/plugins.json") -> str:
        return str(self.server.make_url(path))

    def publish(self, plugin_id: str, version: str, archive: bytes = None, *, sha256: str = None, core: str = "0.1.0"):
        archive = archive if archive is not None else _archive(plugin_id, version)
        name = f"/releases/{plugin_id}-{version}.zip"
        self.archives[name] = archive
        self.entries = [entry for entry in self.entries if entry["id"] != plugin_id]
        self.entries.append({
            "id": plugin_id,
            "name": plugin_id.title(),
            "version": version,
            "description": "",
            "license": "GPL-3.0",
            "requires": {"core": core, "plugin_api": None},
            "release": {"archive": self.url(name), "sha256": sha256 or hashlib.sha256(archive).hexdigest()},
        })

    async def handle(self, request):
        if request.path == "/plugins.json":
            return web.json_response({"schema": 1, "plugins": self.entries})
        if request.path in self.archives:
            return web.Response(body=self.archives[request.path])
        return web.Response(status=404)


async def _serve_catalog() -> Catalog:
    served = Catalog()
    app = web.Application()
    app.router.add_get("/{tail:.*}", served.handle)
    served.server = TestServer(app, host="127.0.0.1")
    await served.server.start_server()
    return served


@pytest.fixture
async def catalog(monkeypatch):
    monkeypatch.setenv(ALLOW_LOCAL_SOURCES_ENV, "1")
    served = await _serve_catalog()
    yield served
    await served.server.close()


@pytest.fixture
async def other_catalog(catalog):
    served = await _serve_catalog()
    yield served
    await served.server.close()


async def _install(repo, registry, served: Catalog, plugin_id: str, **collaborators):
    listed = next((entry for entry in served.entries if entry["id"] == plugin_id), None)
    seen = listed["release"] if listed else {"sha256": "0" * 64}
    return await operations.install_from_catalog(
        repo,
        registry,
        served.url(),
        plugin_id,
        version=listed["version"] if listed else "1.0.0",
        sha256=seen["sha256"],
        **collaborators,
    )


@pytest.fixture
def plugins(tmp_path, mock_db):
    marketplace = tmp_path / "marketplace"
    local = tmp_path / "local"
    marketplace.mkdir()
    local.mkdir()
    registry = PluginRegistry(marketplace_dir=str(marketplace), local_dir=str(local))
    registry.discover_plugins()
    return registry, PluginRepository(), marketplace, local


def _entry(plugin_id="demo", version="1.0.0", core="0.1.0", archive="https://example.com/demo.zip"):
    return CatalogEntry(plugin_id, plugin_id, version, "", "", core, archive, "a" * 64 if archive else None)


def test_an_entry_absent_from_this_install_is_available(plugins):
    registry, _, _, local = plugins

    assert catalog_item(_entry(), registry, local, CATALOG_A).status == "available"


def test_an_entry_bundled_with_potionui_is_not_installable(plugins):
    registry, _, marketplace, local = plugins
    _write_plugin(marketplace / "demo", "demo", "1.0.0", record=False)
    registry.discover_plugins()

    item = catalog_item(_entry(version="2.0.0"), registry, local, CATALOG_A)

    assert (item.status, item.installed_version) == ("bundled", "1.0.0")


def test_a_hand_placed_local_folder_of_the_same_id_is_a_conflict(plugins):
    registry, _, _, local = plugins
    _write_plugin(local / "demo", "demo", "1.0.0", record=False)
    registry.discover_plugins()

    item = catalog_item(_entry(version="2.0.0"), registry, local, CATALOG_A)

    assert item.status == "conflict"
    assert item.reason.startswith("The folder demo was not installed from a catalog")
    assert str(local) not in item.reason


def test_the_same_id_installed_under_another_folder_is_a_conflict(plugins):
    registry, _, _, local = plugins
    _write_plugin(local / "demo-checkout", "demo", "1.0.0", record=False)
    registry.discover_plugins()

    item = catalog_item(_entry(version="2.0.0"), registry, local, CATALOG_A)

    assert item.status == "conflict"
    assert "the folder demo-checkout" in item.reason
    assert str(local.parent) not in item.reason


@pytest.mark.parametrize("installed, listed", [("foo-bar", "foo_bar"), ("foo_bar", "foo-bar")])
def test_an_id_that_shares_the_python_package_of_an_installed_plugin_is_a_conflict(plugins, installed, listed):
    registry, _, _, local = plugins
    _write_plugin(local / installed, installed, "1.0.0", record=True, catalog=CATALOG_A)
    registry.discover_plugins()

    item = catalog_item(_entry(plugin_id=listed), registry, local, CATALOG_B)

    assert item.status == "conflict"
    assert item.reason == f"The installed plugin {installed} uses the same Python package name, foo_bar."


async def test_a_catalog_cannot_install_a_plugin_whose_package_name_another_plugin_already_uses(plugins, catalog):
    registry, repo, _, local = plugins
    _write_plugin(local / "foo-bar", "foo-bar", "1.0.0", record=False)
    registry.discover_plugins()
    catalog.publish("foo_bar", "1.0.0")

    with pytest.raises(CatalogInstallError, match="uses the same Python package name"):
        await _install(repo, registry, catalog, "foo_bar")

    assert sorted(path.name for path in local.iterdir()) == ["foo-bar"]


def test_a_folder_of_that_name_without_a_manifest_is_a_conflict(plugins):
    registry, _, _, local = plugins
    (local / "demo").mkdir()

    assert catalog_item(_entry(), registry, local, CATALOG_A).status == "conflict"


def test_a_catalog_install_of_the_listed_version_is_installed_and_of_another_version_can_be_updated(plugins):
    registry, _, _, local = plugins
    _write_plugin(local / "demo", "demo", "1.0.0", record=True)
    registry.discover_plugins()

    assert catalog_item(_entry(version="1.0.0"), registry, local, CATALOG_A).status == "installed"
    item = catalog_item(_entry(version="1.1.0"), registry, local, CATALOG_A)
    assert (item.status, item.installed_version) == ("update", "1.0.0")


@pytest.mark.parametrize(
    "installed, listed, status",
    [
        ("1.0.0", "1.0.1", "update"),
        ("1.0.0", "1.0.0", "installed"),
        ("1.0", "1.0.0", "installed"),
        ("1.10.0", "1.9.0", "older"),
        ("2.0.0", "2.0.0rc1", "older"),
        ("2.0.0rc1", "2.0.0", "update"),
        ("1.0.0", "1.0.0.post1", "update"),
        ("1.0.0", "latest", "unreadable"),
        ("nightly", "1.0.0", "unreadable"),
    ],
)
def test_only_a_strictly_newer_version_is_an_update(plugins, installed, listed, status):
    registry, _, _, local = plugins
    _write_plugin(local / "demo", "demo", installed, record=True)
    registry.discover_plugins()

    item = catalog_item(_entry(version=listed), registry, local, CATALOG_A)

    assert (item.status, item.installed_version) == (status, installed)
    if status in ("older", "unreadable"):
        assert item.reason


def test_a_plugin_installed_from_one_catalog_is_a_conflict_in_another_catalog_that_lists_its_id(plugins):
    registry, _, _, local = plugins
    _write_plugin(local / "demo", "demo", "1.0.0", record=True, catalog=CATALOG_A)
    registry.discover_plugins()

    for version in ("1.0.0", "9.0.0"):
        item = catalog_item(_entry(version=version), registry, local, CATALOG_B)
        assert (item.status, item.installed_version) == ("conflict", "1.0.0")
        assert "installed from another catalog" in item.reason
        assert "(a.example.com)" in item.reason


def test_an_install_record_without_its_catalog_is_a_conflict(plugins):
    registry, _, _, local = plugins
    _write_plugin(local / "demo", "demo", "1.0.0", record=True, catalog=None)
    registry.discover_plugins()

    item = catalog_item(_entry(version="1.1.0"), registry, local, CATALOG_A)

    assert item.status == "conflict"
    assert "installed from another catalog (unknown)" in item.reason


def test_the_catalog_a_plugin_came_from_is_recognised_whatever_the_case_of_its_host_or_an_explicit_default_port(plugins):
    registry, _, _, local = plugins
    _write_plugin(local / "demo", "demo", "1.0.0", record=True, catalog=CATALOG_A)
    registry.discover_plugins()

    for url in ("HTTPS://A.Example.com/plugins.json", "https://a.example.com:443/plugins.json"):
        assert catalog_item(_entry(version="1.1.0"), registry, local, url).status == "update"
    for url in ("https://a.example.com/other.json", "https://a.example.com/plugins.json?v=2", "https://a.example.com:8443/plugins.json"):
        assert catalog_item(_entry(version="1.1.0"), registry, local, url).status == "conflict"


def test_an_entry_without_a_release_or_for_another_core_is_not_installable(plugins):
    registry, _, _, local = plugins

    assert catalog_item(_entry(archive=None), registry, local, CATALOG_A).status == "unreleased"
    item = catalog_item(_entry(core="9.0.0"), registry, local, CATALOG_A)
    assert item.status == "incompatible"
    assert "needs PotionUI >=9.0.0" in item.reason
    assert catalog_item(_entry(core="02cce6fdd"), registry, local, CATALOG_A).status == "incompatible"


async def test_reading_a_catalog_gives_each_entry_its_status(plugins, catalog):
    registry, _, marketplace, _ = plugins
    _write_plugin(marketplace / "bundled", "bundled", "1.0.0", record=False)
    registry.discover_plugins()
    catalog.publish("demo", "1.0.0")
    catalog.publish("bundled", "2.0.0")
    catalog.publish("future", "1.0.0", core=">=9")

    listing = await operations.read_catalog(registry, catalog.url())

    assert {item.entry.id: item.status for item in listing.items} == {
        "demo": "available",
        "bundled": "bundled",
        "future": "incompatible",
    }


async def test_installing_puts_the_plugin_in_local_with_its_install_record_and_discovers_it_disabled(plugins, catalog):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    preset_loader = Mock()

    result = await _install(repo, registry, catalog, "demo", preset_loader=preset_loader)

    preset_loader.reload.assert_not_called()

    folder = local / "demo"
    assert (folder / "manifest.yml").read_bytes() == _manifest("demo", "1.0.0")
    record = json.loads((folder / INSTALL_RECORD).read_text())
    assert (record["catalog"], record["version"], record["sha256"]) == (
        catalog.url(),
        "1.0.0",
        hashlib.sha256(catalog.archives["/releases/demo-1.0.0.zip"]).hexdigest(),
    )
    assert not (local / STAGING_FOLDER).exists()
    assert registry.get_plugin("demo").version == "1.0.0"
    assert repo.get_plugin_by_id("demo").enabled is False
    assert (result.plugin_id, result.version, result.previous_version, result.enabled) == ("demo", "1.0.0", None, False)
    assert result.dependencies_python == ["numpy>=1.24"]


async def test_a_new_install_over_an_enabled_row_left_by_a_removed_copy_stays_disabled_across_a_restart(plugins, catalog):
    registry, repo, _, local = plugins
    repo.create_plugin(
        Plugin(
            id="demo",
            name="demo",
            version="0.9.0",
            type="backend-only",
            enabled=True,
            manifest_path="manifest.yml",
            installed_at=now_utc(),
            updated_at=now_utc(),
        )
    )
    assert repo.get_plugin_by_id("demo").enabled is True
    catalog.publish("demo", "1.0.0")

    result = await _install(repo, registry, catalog, "demo")

    assert result.enabled is False
    assert repo.get_plugin_by_id("demo").enabled is False
    assert registry.get_plugin_state("demo") != PluginState.ENABLED


def _an_enabled_row_left_by_a_removed_copy(repo) -> None:
    repo.create_plugin(
        Plugin(
            id="demo",
            name="demo",
            version="0.9.0",
            type="backend-only",
            enabled=True,
            manifest_path="manifest.yml",
            installed_at=now_utc(),
            updated_at=now_utc(),
        )
    )


async def test_a_new_install_whose_scan_raises_over_an_enabled_row_left_by_a_removed_copy_leaves_it_disabled(plugins, catalog):
    registry, repo, _, local = plugins
    _a_page_route_owned_by_another_plugin(repo, registry, local)
    _an_enabled_row_left_by_a_removed_copy(repo)
    catalog.publish("demo", "1.0.0", _archive_taking_that_route("demo", "1.0.0"))

    with pytest.raises(CatalogInstallError) as refused:
        await _install(repo, registry, catalog, "demo")

    assert isinstance(refused.value.__cause__, sqlite3.IntegrityError)
    assert (local / "demo" / "payload.py").exists()
    assert repo.get_plugin_by_id("demo").enabled is False
    assert registry.get_plugin_state("demo") != PluginState.ENABLED
    assert str(refused.value).startswith("demo 1.0.0 is in the folder demo, but its scan failed (IntegrityError: ")
    assert str(refused.value).endswith("; it stays there, disabled. Remove that folder to install it again.")


async def test_a_new_install_does_not_carry_over_the_enabled_state_of_a_copy_removed_while_running(plugins, catalog, monkeypatch):
    registry, repo, _, local = plugins
    _write_plugin(local / "demo", "demo", "0.9.0", record=False)
    operations.scan_plugins(repo, registry)
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    shutil.rmtree(local / "demo")
    catalog.publish("demo", "1.0.0")
    real_enable = registry._enable_locked
    started = []

    def enable(plugin_id):
        started.append(registry.get_plugin(plugin_id).version)
        return real_enable(plugin_id)

    monkeypatch.setattr(registry, "_enable_locked", enable)

    result = await _install(repo, registry, catalog, "demo")

    assert started == []
    assert result.enabled is False
    assert registry.get_plugin("demo").version == "1.0.0"
    assert registry.get_plugin_state("demo") != PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is False


@pytest.mark.parametrize("status", ["update", "available"])
async def test_an_install_cancelled_once_the_new_code_is_in_place_leaves_the_plugin_disabled(plugins, catalog, monkeypatch, status):
    registry, repo, _, local = plugins
    if status == "update":
        catalog.publish("demo", "1.0.0")
        await _install(repo, registry, catalog, "demo")
        assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
        catalog.publish("demo", "1.1.0")
    else:
        _an_enabled_row_left_by_a_removed_copy(repo)
        catalog.publish("demo", "1.0.0")
    real_place = install_ops._place

    def place(*args):
        real_place(*args)
        raise asyncio.CancelledError()

    monkeypatch.setattr(install_ops, "_place", place)

    with pytest.raises(asyncio.CancelledError):
        await _install(repo, registry, catalog, "demo")

    assert (local / "demo" / "README.md").read_text() == f"demo {catalog.entries[0]['version']}\n"
    assert repo.get_plugin_by_id("demo").enabled is False
    assert registry.get_plugin_state("demo") != PluginState.ENABLED


def _restarted(marketplace: Path, local: Path) -> PluginRegistry:
    registry = PluginRegistry(marketplace_dir=str(marketplace), local_dir=str(local))
    registry.discover_plugins()
    return registry


@pytest.mark.parametrize("stop", ["cancelled", "scan raises"])
async def test_a_default_enabled_first_install_stopped_before_its_row_exists_is_not_enabled_at_the_next_start(
    plugins, catalog, monkeypatch, stop
):
    registry, repo, marketplace, local = plugins
    catalog.publish("demo", "1.0.0", _archive("demo", "1.0.0", manifest=_manifest("demo", "1.0.0", default_enabled=True)))
    if stop == "cancelled":
        real_place = install_ops._place

        def place(*args):
            real_place(*args)
            raise asyncio.CancelledError()

        monkeypatch.setattr(install_ops, "_place", place)
    else:

        def scan(repo, registry):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(install_ops, "scan_plugins", scan)

    with pytest.raises(BaseException):
        await _install(repo, registry, catalog, "demo")
    assert (local / "demo" / INSTALL_RECORD).exists()
    assert repo.get_plugin_by_id("demo") is None
    after_restart = _restarted(marketplace, local)

    assert operations.enable_default_plugins(repo, after_restart) == []
    assert after_restart.get_plugin_state("demo") != PluginState.ENABLED
    assert repo.get_plugin_by_id("demo") is None


def test_a_hand_placed_default_enabled_plugin_is_still_enabled_at_the_first_start(plugins):
    registry, repo, marketplace, local = plugins
    (local / "demo").mkdir()
    (local / "demo" / "manifest.yml").write_bytes(_manifest("demo", "1.0.0", default_enabled=True))
    after_restart = _restarted(marketplace, local)

    assert operations.enable_default_plugins(repo, after_restart) == ["demo"]
    assert after_restart.get_plugin_state("demo") == PluginState.ENABLED


async def test_an_update_cancelled_once_the_new_code_is_in_place_is_not_enabled_by_the_next_scan(plugins, catalog, monkeypatch):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    catalog.publish("demo", "1.1.0")
    real_place = install_ops._place

    def place(*args):
        real_place(*args)
        raise asyncio.CancelledError()

    monkeypatch.setattr(install_ops, "_place", place)

    with pytest.raises(asyncio.CancelledError):
        await _install(repo, registry, catalog, "demo")
    assert registry.get_plugin_state("demo") != PluginState.ENABLED
    operations.scan_plugins(repo, registry)

    assert registry.get_plugin("demo").version == "1.1.0"
    assert registry.get_plugin_state("demo") != PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is False


async def test_an_update_whose_scan_raises_and_whose_put_back_is_cancelled_leaves_the_plugin_disabled(plugins, catalog, monkeypatch):
    registry, repo, _, local = plugins
    _a_page_route_owned_by_another_plugin(repo, registry, local)
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    catalog.publish("demo", "1.1.0", _archive_taking_that_route("demo", "1.1.0"))
    real_put_back = install_ops._put_previous_back

    def put_back(*args):
        real_put_back(*args)
        raise asyncio.CancelledError()

    monkeypatch.setattr(install_ops, "_put_previous_back", put_back)

    with pytest.raises(asyncio.CancelledError):
        await _install(repo, registry, catalog, "demo")

    assert (local / "demo" / "README.md").read_text() == "demo 1.0.0\n"
    assert registry.get_plugin_state("demo") != PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is False


async def test_an_update_whose_request_is_cancelled_while_the_files_move_finishes_before_it_lets_the_lock_go(
    plugins, catalog, monkeypatch
):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    catalog.publish("demo", "1.1.0")
    moving, release = threading.Event(), threading.Event()
    real_place = install_ops._place

    def place(*args):
        moving.set()
        assert release.wait(10)
        return real_place(*args)

    monkeypatch.setattr(install_ops, "_place", place)
    request = asyncio.ensure_future(_install(repo, registry, catalog, "demo"))
    assert await asyncio.to_thread(moving.wait, 10)

    request.cancel()
    await asyncio.sleep(0.2)
    assert not request.done()
    assert install_ops._install_lock.locked()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await request
    assert not install_ops._install_lock.locked()
    assert (local / "demo" / "README.md").read_text() == "demo 1.1.0\n"
    assert [path.name for path in local.iterdir()] == ["demo"]
    assert registry.get_plugin("demo").version == "1.1.0"
    assert registry.get_plugin_state("demo") == PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is True


async def test_an_update_whose_row_cannot_be_disabled_after_unpacking_leaves_no_staging_folder(plugins, catalog, monkeypatch):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    catalog.publish("demo", "1.1.0")

    def locked(plugin_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repo, "disable_plugin", locked)

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        await _install(repo, registry, catalog, "demo")

    assert [path.name for path in local.iterdir()] == ["demo"]
    assert (local / "demo" / "README.md").read_text() == "demo 1.0.0\n"
    assert registry.get_plugin_state("demo") == PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is True


async def test_an_update_stopped_while_the_archive_is_unpacked_leaves_the_installed_version_enabled(plugins, catalog, monkeypatch):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    catalog.publish("demo", "1.1.0")
    rows_while_unpacking = []
    real_unpack = install_ops.unpack_plugin_archive

    def unpack(*args):
        rows_while_unpacking.append(repo.get_plugin_by_id("demo").enabled)
        real_unpack(*args)
        raise asyncio.CancelledError()

    monkeypatch.setattr(install_ops, "unpack_plugin_archive", unpack)

    with pytest.raises(asyncio.CancelledError):
        await _install(repo, registry, catalog, "demo")

    assert rows_while_unpacking == [True]
    assert (local / "demo" / "README.md").read_text() == "demo 1.0.0\n"
    assert [path.name for path in local.iterdir()] == ["demo"]
    assert registry.get_plugin_state("demo") == PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is True


@pytest.mark.parametrize("status", ["update", "available"])
async def test_an_install_whose_placement_fails_leaves_the_row_as_it_was(plugins, catalog, status):
    registry, repo, _, local = plugins
    if status == "update":
        catalog.publish("demo", "1.0.0")
        await _install(repo, registry, catalog, "demo")
        assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
        catalog.publish("demo", "1.1.0", _archive("demo", "1.1.0", manifest=_manifest("demo", "6.6.6")))
    else:
        _an_enabled_row_left_by_a_removed_copy(repo)
        catalog.publish("demo", "1.0.0", _archive("demo", "1.0.0", manifest=_manifest("demo", "6.6.6")))

    with pytest.raises(CatalogInstallError, match="holds demo 6.6.6"):
        await _install(repo, registry, catalog, "demo")

    assert repo.get_plugin_by_id("demo").enabled is True


async def test_an_archive_whose_sha256_differs_from_the_catalog_writes_nothing(plugins, catalog):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0", sha256="0" * 64)

    with pytest.raises(CatalogInstallError, match="sha256"):
        await _install(repo, registry, catalog, "demo")

    assert list(local.iterdir()) == []
    assert registry.get_plugin("demo") is None


async def test_an_archive_that_holds_another_plugin_or_version_writes_nothing(plugins, catalog):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0", _archive("demo", "1.0.0", manifest=_manifest("demo", "6.6.6")))

    with pytest.raises(CatalogInstallError, match="holds demo 6.6.6"):
        await _install(repo, registry, catalog, "demo")

    assert list(local.iterdir()) == []


async def test_an_archive_with_a_path_outside_its_folder_writes_nothing(plugins, catalog, tmp_path):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0", _archive("demo", "1.0.0", extra=[("demo/../../escape.py", b"x")]))

    with pytest.raises(ValueError, match="'..' part"):
        await _install(repo, registry, catalog, "demo")

    assert list(local.iterdir()) == []
    assert not (tmp_path / "escape.py").exists()


async def test_an_archive_with_an_invalid_manifest_writes_nothing(plugins, catalog):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0", _archive("demo", "1.0.0", manifest=_manifest("demo", "1.0.0", unknown_key=True)))

    with pytest.raises(CatalogInstallError, match="manifest in the archive is not valid"):
        await _install(repo, registry, catalog, "demo")

    assert list(local.iterdir()) == []


async def test_updating_replaces_the_catalog_install_and_keeps_the_plugin_enabled(plugins, catalog):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    catalog.publish("demo", "1.1.0")
    preset_loader = Mock()

    result = await _install(repo, registry, catalog, "demo", preset_loader=preset_loader)

    preset_loader.reload.assert_called_once_with()
    assert (local / "demo" / "README.md").read_text() == "demo 1.1.0\n"
    assert json.loads((local / "demo" / INSTALL_RECORD).read_text())["version"] == "1.1.0"
    assert registry.get_plugin("demo").version == "1.1.0"
    assert registry.get_plugin_state("demo") == PluginState.ENABLED
    assert (result.previous_version, result.enabled) == ("1.0.0", True)
    assert [path.name for path in local.iterdir()] == ["demo"]


def _unstartable_archive(plugin_id: str, version: str) -> bytes:
    manifest = _manifest(plugin_id, version, dependencies={"python": ["potionui-catalog-missing-package>=1"]})
    return _archive(plugin_id, version, manifest=manifest, extra=[(f"{plugin_id}/payload.py", b"print('new')")])


async def test_an_update_that_does_not_start_puts_the_previous_version_back_and_running(plugins, catalog):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    catalog.publish("demo", "1.1.0", _unstartable_archive("demo", "1.1.0"))
    preset_loader = Mock()

    with pytest.raises(CatalogInstallError) as refused:
        await _install(repo, registry, catalog, "demo", preset_loader=preset_loader)

    assert "demo 1.1.0 did not start (Missing dependencies: potionui-catalog-missing-package>=1)" in str(refused.value)
    assert str(refused.value).endswith(
        "the installed 1.0.0 is back in place and running. Restart PotionUI to unload any module 1.1.0 imported."
    )
    assert (local / "demo" / "README.md").read_text() == "demo 1.0.0\n"
    assert not (local / "demo" / "payload.py").exists()
    assert json.loads((local / "demo" / INSTALL_RECORD).read_text())["version"] == "1.0.0"
    assert registry.get_plugin("demo").version == "1.0.0"
    assert registry.get_plugin_state("demo") == PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is True
    assert [path.name for path in local.iterdir()] == ["demo"]
    preset_loader.reload.assert_called()


async def test_an_update_whose_scan_fails_puts_the_previous_version_back_and_running(plugins, catalog):
    registry, repo, _, local = plugins
    other = local / "other"
    other.mkdir()
    (other / "manifest.yml").write_bytes(
        _manifest("other", "1.0.0", pages=[{"route": "shared-page", "component": "Page", "label": "Other"}])
    )
    operations.scan_plugins(repo, registry)
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    taken = _manifest("demo", "1.1.0", pages=[{"route": "shared-page", "component": "Page", "label": "Demo"}])
    catalog.publish("demo", "1.1.0", _archive("demo", "1.1.0", manifest=taken, extra=[("demo/payload.py", b"print('new')")]))
    preset_loader = Mock()

    with pytest.raises(CatalogInstallError) as refused:
        await _install(repo, registry, catalog, "demo", preset_loader=preset_loader)

    assert "demo 1.1.0 did not start (the scan failed: IntegrityError" in str(refused.value)
    assert str(refused.value).endswith(
        "the installed 1.0.0 is back in place and running. Restart PotionUI to unload any module 1.1.0 imported."
    )
    assert (local / "demo" / "README.md").read_text() == "demo 1.0.0\n"
    assert not (local / "demo" / "payload.py").exists()
    assert registry.get_plugin("demo").version == "1.0.0"
    assert registry.get_plugin_state("demo") == PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is True
    assert sorted(path.name for path in local.iterdir()) == ["demo", "other"]
    preset_loader.reload.assert_called()


async def test_a_disabled_plugin_takes_an_update_its_new_code_would_not_start_with_since_nothing_runs_it(plugins, catalog):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    catalog.publish("demo", "1.1.0", _unstartable_archive("demo", "1.1.0"))

    result = await _install(repo, registry, catalog, "demo")

    assert (result.version, result.previous_version, result.enabled) == ("1.1.0", "1.0.0", False)
    assert (local / "demo" / "payload.py").exists()
    assert registry.get_plugin("demo").version == "1.1.0"
    assert [path.name for path in local.iterdir()] == ["demo"]


def _discovery_that_skips_demo_once(registry, monkeypatch) -> None:
    real_discover = registry.loader.discover_plugins
    calls = []

    def discover():
        calls.append(None)
        found = real_discover()
        return [manifest for manifest in found if manifest.id != "demo"] if len(calls) == 1 else found

    monkeypatch.setattr(registry.loader, "discover_plugins", discover)


async def test_an_update_of_a_disabled_plugin_whose_new_manifest_the_scan_skips_puts_the_previous_version_back(
    plugins, catalog, monkeypatch
):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    catalog.publish("demo", "1.1.0")
    _discovery_that_skips_demo_once(registry, monkeypatch)

    with pytest.raises(CatalogInstallError) as refused:
        await _install(repo, registry, catalog, "demo")

    assert (local / "demo" / "README.md").read_text() == "demo 1.0.0\n"
    assert [path.name for path in local.iterdir()] == ["demo"]
    assert registry.get_plugin("demo").version == "1.0.0"
    assert registry.get_plugin_state("demo") != PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is False
    assert str(refused.value) == (
        "demo 1.1.0 did not start (the scan did not find its manifest); the installed 1.0.0 is back in place."
    )


def _discovery_that_raises_once(registry, monkeypatch) -> None:
    real_discover = registry.loader.discover_plugins
    calls = []

    def discover():
        calls.append(None)
        if len(calls) == 1:
            raise PermissionError("the plugin folder cannot be listed")
        return real_discover()

    monkeypatch.setattr(registry.loader, "discover_plugins", discover)


async def test_an_update_whose_scan_changes_nothing_puts_the_previous_version_back_and_running(plugins, catalog, monkeypatch):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    catalog.publish("demo", "1.1.0")
    _discovery_that_raises_once(registry, monkeypatch)

    with pytest.raises(CatalogInstallError) as refused:
        await _install(repo, registry, catalog, "demo")

    assert (local / "demo" / "README.md").read_text() == "demo 1.0.0\n"
    assert [path.name for path in local.iterdir()] == ["demo"]
    assert registry.get_plugin("demo").version == "1.0.0"
    assert registry.get_plugin_state("demo") == PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is True
    assert str(refused.value).startswith(
        "demo 1.1.0 did not start (the scan did not pick up its manifest); the installed 1.0.0 is back in place and running."
    )


async def test_a_new_install_whose_scan_changes_nothing_says_the_folder_stays_disabled(plugins, catalog, monkeypatch):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    _discovery_that_raises_once(registry, monkeypatch)

    with pytest.raises(CatalogInstallError) as refused:
        await _install(repo, registry, catalog, "demo")

    assert str(refused.value) == (
        "demo 1.0.0 is in the folder demo, but the scan did not find its manifest; it stays there, disabled. "
        "Remove that folder to install it again."
    )
    assert [path.name for path in local.iterdir()] == ["demo"]
    assert registry.get_plugin("demo") is None
    operations.scan_plugins(repo, registry)
    assert registry.get_plugin_state("demo") != PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is False


async def test_an_update_that_does_not_start_and_cannot_be_undone_keeps_the_previous_version_in_staging(plugins, catalog, monkeypatch):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    catalog.publish("demo", "1.1.0", _unstartable_archive("demo", "1.1.0"))
    real_replace = install_ops.os.replace

    def replace(source, target):
        if Path(source).name == "previous" and Path(target) == local / "demo":
            raise OSError("the folder is in use")
        return real_replace(source, target)

    monkeypatch.setattr(install_ops.os, "replace", replace)

    with pytest.raises(CatalogInstallError, match="could not be put back .*in use.*; it is kept in .potionui-install/"):
        await _install(repo, registry, catalog, "demo")

    kept = list((local / STAGING_FOLDER).glob("*/previous"))
    assert len(kept) == 1
    assert (kept[0] / "README.md").read_text() == "demo 1.0.0\n"
    assert (local / "demo" / "payload.py").exists()


def _a_page_route_owned_by_another_plugin(repo, registry, local: Path) -> None:
    other = local / "other"
    other.mkdir()
    (other / "manifest.yml").write_bytes(
        _manifest("other", "1.0.0", pages=[{"route": "shared-page", "component": "Page", "label": "Other"}])
    )
    operations.scan_plugins(repo, registry)


def _archive_taking_that_route(plugin_id: str, version: str) -> bytes:
    taken = _manifest(plugin_id, version, pages=[{"route": "shared-page", "component": "Page", "label": "Demo"}])
    return _archive(plugin_id, version, manifest=taken, extra=[(f"{plugin_id}/payload.py", b"print('new')")])


@pytest.mark.parametrize("failure", ["does not start", "scan raises"])
async def test_an_update_that_fails_and_cannot_be_put_back_leaves_the_new_code_disabled(plugins, catalog, monkeypatch, failure):
    registry, repo, _, local = plugins
    if failure == "scan raises":
        _a_page_route_owned_by_another_plugin(repo, registry, local)
        new = _archive_taking_that_route("demo", "1.1.0")
    else:
        new = _unstartable_archive("demo", "1.1.0")
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    catalog.publish("demo", "1.1.0", new)
    real_replace = install_ops.os.replace

    def replace(source, target):
        if Path(source).name == "previous" and Path(target) == local / "demo":
            raise OSError("the folder is in use")
        return real_replace(source, target)

    monkeypatch.setattr(install_ops.os, "replace", replace)

    with pytest.raises(CatalogInstallError) as refused:
        await _install(repo, registry, catalog, "demo")

    assert (local / "demo" / "payload.py").exists()
    assert len(list((local / STAGING_FOLDER).glob("*/previous"))) == 1
    assert registry.get_plugin_state("demo") != PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is False
    assert "could not be put back (the folder is in use); it is kept in .potionui-install/" in str(refused.value)
    assert str(refused.value).endswith("previous, and demo is disabled.")


async def test_an_update_whose_move_and_move_back_both_fail_leaves_the_plugin_disabled(plugins, catalog, monkeypatch):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    catalog.publish("demo", "1.1.0")
    real_replace = install_ops.os.replace

    def replace(source, target):
        if Path(target) == local / "demo":
            raise OSError("the folder is in use")
        return real_replace(source, target)

    monkeypatch.setattr(install_ops.os, "replace", replace)

    with pytest.raises(CatalogInstallError) as refused:
        await _install(repo, registry, catalog, "demo")

    assert not (local / "demo").exists()
    kept = list((local / STAGING_FOLDER).glob("*/previous"))
    assert len(kept) == 1
    assert (kept[0] / "README.md").read_text() == "demo 1.0.0\n"
    assert registry.get_plugin_state("demo") != PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is False
    assert str(refused.value).endswith("/previous. The plugin demo is disabled.")


async def test_an_update_whose_first_move_fails_leaves_the_installed_version_in_place_and_running(plugins, catalog, monkeypatch):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    catalog.publish("demo", "1.1.0")
    real_replace = install_ops.os.replace

    def replace(source, target):
        if Path(source) == local / "demo":
            raise OSError("the folder is in use")
        return real_replace(source, target)

    monkeypatch.setattr(install_ops.os, "replace", replace)

    with pytest.raises(OSError, match="in use"):
        await _install(repo, registry, catalog, "demo")

    assert (local / "demo" / "README.md").read_text() == "demo 1.0.0\n"
    assert [path.name for path in local.iterdir()] == ["demo"]
    assert registry.get_plugin("demo").version == "1.0.0"
    assert registry.get_plugin_state("demo") == PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is True


async def test_an_update_whose_restored_version_does_not_start_again_leaves_it_in_place_and_disabled(plugins, catalog, monkeypatch):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    catalog.publish("demo", "1.1.0", _unstartable_archive("demo", "1.1.0"))
    monkeypatch.setattr(registry, "enable_plugin", lambda plugin_id: False)

    with pytest.raises(CatalogInstallError, match="the installed 1.0.0 is back in place, but did not start again either"):
        await _install(repo, registry, catalog, "demo")

    assert (local / "demo" / "README.md").read_text() == "demo 1.0.0\n"
    assert [path.name for path in local.iterdir()] == ["demo"]
    assert registry.get_plugin("demo").version == "1.0.0"
    assert registry.get_plugin_state("demo") != PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is False


@pytest.mark.parametrize("restore", ["scan raises", "scan changes nothing"])
async def test_an_update_whose_restore_scan_fails_leaves_the_installed_version_in_place_and_disabled(
    plugins, catalog, monkeypatch, restore
):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    catalog.publish("demo", "1.1.0", _unstartable_archive("demo", "1.1.0"))
    real_discover = registry.loader.discover_plugins
    real_scan = install_ops.scan_plugins
    scans = []

    def denied():
        raise PermissionError("the plugin folder cannot be listed")

    def scan(repo, registry):
        scans.append(None)
        if len(scans) == 2 and restore == "scan raises":
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(registry.loader, "discover_plugins", denied if len(scans) == 2 else real_discover)
        return real_scan(repo, registry)

    monkeypatch.setattr(install_ops, "scan_plugins", scan)

    with pytest.raises(CatalogInstallError) as refused:
        await _install(repo, registry, catalog, "demo")

    assert (local / "demo" / "README.md").read_text() == "demo 1.0.0\n"
    assert [path.name for path in local.iterdir()] == ["demo"]
    assert registry.get_plugin_state("demo") != PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is False
    if restore == "scan raises":
        reason = "the scan that followed failed (OperationalError: database is locked)"
    else:
        reason = "the scan did not pick it up"
    assert str(refused.value).endswith(f"the installed 1.0.0 is back in place, but {reason}, so demo is disabled.")


async def test_a_new_install_that_fails_after_taking_down_an_earlier_copy_leaves_its_row_disabled(plugins, catalog, monkeypatch):
    registry, repo, _, local = plugins
    _write_plugin(local / "demo", "demo", "0.9.0", record=False)
    operations.scan_plugins(repo, registry)
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    shutil.rmtree(local / "demo")
    catalog.publish("demo", "1.0.0")
    real_replace = install_ops.os.replace

    def replace(source, target):
        if Path(target) == local / "demo":
            raise OSError("the folder is in use")
        return real_replace(source, target)

    monkeypatch.setattr(install_ops.os, "replace", replace)

    with pytest.raises(OSError, match="in use"):
        await _install(repo, registry, catalog, "demo")

    assert registry.get_plugin_state("demo") != PluginState.ENABLED
    assert repo.get_plugin_by_id("demo").enabled is False


async def test_a_failed_update_leaves_the_installed_version_in_place(plugins, catalog):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    catalog.publish("demo", "1.1.0", _archive("demo", "1.1.0", extra=[("demo/CON", b"x")]))

    with pytest.raises(ValueError):
        await _install(repo, registry, catalog, "demo")

    assert (local / "demo" / "README.md").read_text() == "demo 1.0.0\n"
    assert [path.name for path in local.iterdir()] == ["demo"]


@pytest.mark.parametrize("change", ["archive", "version"])
async def test_a_catalog_that_changes_after_the_admin_confirmed_installs_nothing(plugins, catalog, change):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    confirmed = dict(catalog.entries[0])
    if change == "archive":
        catalog.publish("demo", "1.0.0", _archive("demo", "1.0.0", extra=[("demo/payload.py", b"print('swapped')")]))
    else:
        catalog.publish("demo", "1.1.0")

    with pytest.raises(CatalogInstallError, match="not the 1.0.0 with the sha256 .* you confirmed; nothing was installed"):
        await operations.install_from_catalog(
            repo,
            registry,
            catalog.url(),
            "demo",
            version=confirmed["version"],
            sha256=confirmed["release"]["sha256"],
        )

    assert list(local.iterdir()) == []
    assert registry.get_plugin("demo") is None


async def test_an_update_installs_only_the_version_and_archive_the_admin_confirmed(plugins, catalog):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    catalog.publish("demo", "1.1.0")
    confirmed = dict(catalog.entries[0])
    catalog.publish("demo", "1.2.0")

    with pytest.raises(CatalogInstallError, match="now lists demo 1.2.0"):
        await operations.install_from_catalog(
            repo,
            registry,
            catalog.url(),
            "demo",
            version=confirmed["version"],
            sha256=confirmed["release"]["sha256"],
        )

    assert (local / "demo" / "README.md").read_text() == "demo 1.0.0\n"
    assert registry.get_plugin("demo").version == "1.0.0"


async def test_a_catalog_that_goes_back_to_an_older_version_cannot_downgrade_the_installed_plugin(plugins, catalog):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.1.0")
    await _install(repo, registry, catalog, "demo")
    catalog.publish("demo", "1.0.0")

    listing = await operations.read_catalog(registry, catalog.url())
    assert [(item.status, item.installed_version) for item in listing.items] == [("older", "1.1.0")]
    with pytest.raises(CatalogInstallError, match="older than the installed 1.1.0"):
        await _install(repo, registry, catalog, "demo")

    assert (local / "demo" / "README.md").read_text() == "demo 1.1.0\n"
    assert registry.get_plugin("demo").version == "1.1.0"


async def test_another_catalog_that_lists_the_same_id_cannot_replace_the_installed_plugin(plugins, catalog, other_catalog):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    await _install(repo, registry, catalog, "demo")
    assert repo.enable_plugin("demo") and registry.enable_plugin("demo")
    other_catalog.publish("demo", "2.0.0", _archive("demo", "2.0.0", extra=[("demo/payload.py", b"print('other')")]))

    listing = await operations.read_catalog(registry, other_catalog.url())
    assert [(item.status, item.installed_version) for item in listing.items] == [("conflict", "1.0.0")]
    with pytest.raises(CatalogInstallError, match="installed from another catalog"):
        await _install(repo, registry, other_catalog, "demo")

    assert (local / "demo" / "README.md").read_text() == "demo 1.0.0\n"
    assert not (local / "demo" / "payload.py").exists()
    assert json.loads((local / "demo" / INSTALL_RECORD).read_text())["catalog"] == catalog.url()
    assert registry.get_plugin("demo").version == "1.0.0"
    assert registry.get_plugin_state("demo") == PluginState.ENABLED


@pytest.mark.parametrize("status", ["installed", "conflict", "bundled", "incompatible"])
async def test_an_entry_that_is_not_installable_is_refused_with_its_reason(plugins, catalog, status):
    registry, repo, marketplace, local = plugins
    if status == "installed":
        catalog.publish("demo", "1.0.0")
        await _install(repo, registry, catalog, "demo")
    elif status == "conflict":
        _write_plugin(local / "demo", "demo", "0.9.0", record=False)
        catalog.publish("demo", "1.0.0")
    elif status == "bundled":
        _write_plugin(marketplace / "demo", "demo", "0.9.0", record=False)
        catalog.publish("demo", "1.0.0")
    else:
        catalog.publish("demo", "1.0.0", core=">=9")
    registry.discover_plugins()
    before = sorted(path.name for path in local.rglob("*"))

    with pytest.raises(CatalogInstallError):
        await _install(repo, registry, catalog, "demo")

    assert sorted(path.name for path in local.rglob("*")) == before


async def test_a_plugin_the_catalog_does_not_list_is_refused(plugins, catalog):
    registry, repo, _, _ = plugins
    catalog.publish("demo", "1.0.0")

    with pytest.raises(CatalogInstallError, match="lists no installable plugin 'other'"):
        await _install(repo, registry, catalog, "other")


def test_a_conflict_with_another_catalog_names_its_host_and_not_its_query(plugins):
    registry, _, _, local = plugins
    _write_plugin(local / "demo", "demo", "1.0.0", record=True, catalog="https://a.example.com/plugins.json?token=s3cret-value")
    registry.discover_plugins()

    item = catalog_item(_entry(version="1.1.0"), registry, local, CATALOG_B)

    assert item.status == "conflict"
    assert "(a.example.com)" in item.reason
    assert "s3cret-value" not in item.reason


async def test_an_install_from_a_catalog_url_with_a_query_logs_its_host_only(plugins, catalog, monkeypatch):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    url = catalog.url("/plugins.json?token=s3cret-value")
    logger = Mock()
    monkeypatch.setattr(install_ops, "logger", logger)

    await operations.install_from_catalog(
        repo, registry, url, "demo", version="1.0.0", sha256=catalog.entries[0]["release"]["sha256"]
    )

    logged = [str(argument) for call in logger.method_calls for argument in call.args]
    assert "Installed plugin demo 1.0.0 from a catalog on 127.0.0.1" in logged
    assert not any("s3cret-value" in message for message in logged)
    assert json.loads((local / "demo" / INSTALL_RECORD).read_text())["catalog"] == url


async def test_a_local_catalog_is_refused_without_the_explicit_setting(plugins, catalog, monkeypatch):
    registry, repo, _, local = plugins
    catalog.publish("demo", "1.0.0")
    monkeypatch.delenv(ALLOW_LOCAL_SOURCES_ENV)

    with pytest.raises(FetchError, match="Only https URLs"):
        await _install(repo, registry, catalog, "demo")
    assert list(local.iterdir()) == []


def test_an_update_of_a_folder_that_lost_its_install_record_replaces_nothing(plugins):
    registry, _, _, local = plugins
    _write_plugin(local / "demo", "demo", "1.0.0", record=False)
    item = CatalogItem(_entry(version="1.1.0"), "update", installed_version="1.0.0")

    with pytest.raises(CatalogInstallError, match="^The folder demo changed since the catalog was read"):
        install_ops._place(install_ops._stage(_archive("demo", "1.1.0"), item, local, "https://example.com/plugins.json"), item, local)

    assert (local / "demo" / "manifest.yml").read_bytes() == _manifest("demo", "1.0.0")
    assert [path.name for path in local.iterdir()] == ["demo"]


def test_a_new_install_over_a_folder_that_appeared_meanwhile_replaces_nothing(plugins):
    registry, _, _, local = plugins
    _write_plugin(local / "demo", "demo", "0.1.0", record=False)
    item = CatalogItem(_entry(), "available")

    with pytest.raises(CatalogInstallError, match="^The folder demo appeared since the catalog was read"):
        install_ops._place(install_ops._stage(_archive("demo", "1.0.0"), item, local, "https://example.com/plugins.json"), item, local)

    assert (local / "demo" / "manifest.yml").read_bytes() == _manifest("demo", "0.1.0")
    assert [path.name for path in local.iterdir()] == ["demo"]


def test_an_update_whose_move_fails_puts_the_installed_version_back(plugins, monkeypatch):
    registry, _, _, local = plugins
    _write_plugin(local / "demo", "demo", "1.0.0", record=True)
    item = CatalogItem(_entry(version="1.1.0"), "update", installed_version="1.0.0")
    real_replace = install_ops.os.replace

    def replace(source, target):
        if Path(target) == local / "demo" and Path(source).name == "demo":
            raise OSError("the folder is in use")
        return real_replace(source, target)

    monkeypatch.setattr(install_ops.os, "replace", replace)

    with pytest.raises(OSError, match="in use"):
        install_ops._place(install_ops._stage(_archive("demo", "1.1.0"), item, local, "https://example.com/plugins.json"), item, local)

    assert (local / "demo" / "manifest.yml").read_bytes() == _manifest("demo", "1.0.0")
    assert [path.name for path in local.iterdir()] == ["demo"]


def test_an_update_whose_move_and_move_back_both_fail_keeps_the_installed_version_in_staging(plugins, monkeypatch):
    registry, _, _, local = plugins
    _write_plugin(local / "demo", "demo", "1.0.0", record=True)
    item = CatalogItem(_entry(version="1.1.0"), "update", installed_version="1.0.0")
    real_replace = install_ops.os.replace

    def replace(source, target):
        if Path(target) == local / "demo":
            raise OSError("the folder is in use")
        return real_replace(source, target)

    monkeypatch.setattr(install_ops.os, "replace", replace)

    with pytest.raises(CatalogInstallError, match="^The folder demo could not be replaced .*in use.* nor put back .*in use.*; the installed 1.0.0 is kept in .potionui-install/.*/previous.$"):
        install_ops._place(install_ops._stage(_archive("demo", "1.1.0"), item, local, "https://example.com/plugins.json"), item, local)

    kept = list((local / STAGING_FOLDER).glob("*/previous"))
    assert len(kept) == 1
    assert (kept[0] / "manifest.yml").read_bytes() == _manifest("demo", "1.0.0")
