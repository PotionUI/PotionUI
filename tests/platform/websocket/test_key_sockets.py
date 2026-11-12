import asyncio
from datetime import timedelta

from types import SimpleNamespace

import pytest

from src.platform.database.rows import dt_iso, now_utc
from src.platform.security import key_revocation
from src.platform.security.api_scopes import ApiScope
from src.platform.security.principal import KeyGrant, ResolvedKey
from src.platform.security.user import AccountType
from src.platform.websocket.key_sockets import (
    EXPIRED_REASON,
    KEY_CLOSE_CODE,
    NARROWED_REASON,
    REVOKED_REASON,
    KeySocketRegistry,
    key_sockets,
)


class FakeSocket:
    def __init__(self):
        self.closed = []

    async def close(self, code=1000, reason=""):
        self.closed.append((code, reason))


def _grant(key_id="k1", expires_at=None):
    return KeyGrant(
        key_id=key_id, user_id="u1", scopes=frozenset({ApiScope.READ}),
        display_prefix="pui_abcd", expires_at=expires_at,
    )


async def _settle():
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_revoking_a_key_closes_its_sockets_with_4001_and_leaves_others_open():
    registry = KeySocketRegistry()
    mine, same_key, other_key = FakeSocket(), FakeSocket(), FakeSocket()
    registry._register(mine, _grant("k1"))
    registry._register(same_key, _grant("k1"))
    registry._register(other_key, _grant("k2"))

    registry.close_for_key("k1")
    await _settle()

    assert mine.closed == [(KEY_CLOSE_CODE, REVOKED_REASON)]
    assert same_key.closed == [(KEY_CLOSE_CODE, REVOKED_REASON)]
    assert other_key.closed == []
    assert registry.count_for_key("k1") == 0
    assert registry.count_for_key("k2") == 1


@pytest.mark.asyncio
async def test_a_revocation_signalled_from_a_worker_thread_closes_on_the_sockets_loop():
    registry = KeySocketRegistry()
    socket = FakeSocket()
    registry._register(socket, _grant("k1"))

    await asyncio.to_thread(registry.close_for_key, "k1")
    await _settle()

    assert socket.closed == [(KEY_CLOSE_CODE, REVOKED_REASON)]


@pytest.mark.asyncio
async def test_the_shared_registry_listens_for_key_revocation():
    socket = FakeSocket()
    key_sockets._register(socket, _grant("listened"))

    key_revocation.notify("listened")
    await _settle()

    assert socket.closed == [(KEY_CLOSE_CODE, REVOKED_REASON)]
    key_sockets._unregister(socket)


@pytest.mark.asyncio
async def test_sweep_closes_only_the_sockets_of_expired_keys():
    registry = KeySocketRegistry()
    expired, live, forever = FakeSocket(), FakeSocket(), FakeSocket()
    now = now_utc()
    registry._register(expired, _grant("k1", dt_iso(now - timedelta(seconds=1))))
    registry._register(live, _grant("k2", dt_iso(now + timedelta(days=1))))
    registry._register(forever, _grant("k3", None))

    assert registry.sweep() == 1
    await _settle()

    assert expired.closed == [(KEY_CLOSE_CODE, EXPIRED_REASON)]
    assert live.closed == [] and forever.closed == []


@pytest.mark.asyncio
async def test_the_heartbeat_sweeps_without_anyone_calling_it():
    registry = KeySocketRegistry(heartbeat_seconds=0.02)
    socket = FakeSocket()
    registry._register(socket, _grant("k1", dt_iso(now_utc() + timedelta(milliseconds=30))))

    await asyncio.sleep(0.3)

    assert socket.closed == [(KEY_CLOSE_CODE, EXPIRED_REASON)]


@pytest.mark.asyncio
async def test_an_unregistered_socket_is_left_alone():
    registry = KeySocketRegistry()
    socket = FakeSocket()
    registry._register(socket, _grant("k1"))
    registry._unregister(socket)

    registry.close_for_key("k1")
    await _settle()

    assert socket.closed == []


@pytest.mark.asyncio
async def test_a_session_socket_is_never_tracked():
    registry = KeySocketRegistry()
    socket = FakeSocket()

    with registry.track(socket, SimpleNamespace(key=None, account_type=AccountType.USER)):
        registry.close_for_key("k1")
        assert registry.sweep() == 0
    await _settle()

    assert socket.closed == []


@pytest.mark.asyncio
async def test_track_unregisters_on_exit():
    registry = KeySocketRegistry()
    socket = FakeSocket()

    with registry.track(socket, SimpleNamespace(key=_grant("k1"), account_type=AccountType.USER)):
        assert registry.count_for_key("k1") == 1

    assert registry.count_for_key("k1") == 0


class Resolver:
    def __init__(self):
        self.live = {}

    def resolve_id(self, key_id):
        return self.live.get(key_id)

    def set(self, key_id, scopes, expires_at=None, account_type=AccountType.USER):
        grant = KeyGrant(key_id, "u1", frozenset(scopes), "pui_abcd", expires_at)
        self.live[key_id] = ResolvedKey(user=SimpleNamespace(account_type=account_type), grant=grant)


ADMIN = frozenset({ApiScope.ADMIN})
READ = frozenset({ApiScope.READ})


def _tracked_registry(resolver):
    registry = KeySocketRegistry()
    registry.set_resolver(resolver)
    return registry


@pytest.mark.asyncio
async def test_narrowing_a_key_closes_its_admin_socket_and_keeps_its_read_socket():
    resolver = Resolver()
    resolver.set("k1", {ApiScope.ADMIN})
    registry = _tracked_registry(resolver)
    admin_socket, read_socket = FakeSocket(), FakeSocket()
    registry._register(admin_socket, _grant("k1"), ADMIN)
    registry._register(read_socket, _grant("k1"), READ)
    await _settle()
    assert admin_socket.closed == [] and read_socket.closed == []

    resolver.set("k1", {ApiScope.READ})
    registry.check_key("k1")
    await _settle()

    assert admin_socket.closed == [(KEY_CLOSE_CODE, NARROWED_REASON)]
    assert read_socket.closed == []


@pytest.mark.asyncio
async def test_the_sweep_closes_sockets_a_changed_ceiling_no_longer_allows():
    resolver = Resolver()
    resolver.set("k1", {ApiScope.ADMIN})
    registry = _tracked_registry(resolver)
    admin_socket = FakeSocket()
    registry._register(admin_socket, _grant("k1"), ADMIN)
    await _settle()

    resolver.set("k1", {ApiScope.READ})
    assert registry.sweep() == 1
    await _settle()

    assert admin_socket.closed == [(KEY_CLOSE_CODE, NARROWED_REASON)]


@pytest.mark.asyncio
async def test_the_sweep_closes_the_sockets_of_a_key_that_vanished_or_was_switched_off():
    resolver = Resolver()
    resolver.set("k1", {ApiScope.READ})
    registry = _tracked_registry(resolver)
    socket = FakeSocket()
    registry._register(socket, _grant("k1"), READ)
    await _settle()

    del resolver.live["k1"]
    registry.sweep()
    await _settle()

    assert socket.closed == [(KEY_CLOSE_CODE, REVOKED_REASON)]


@pytest.mark.asyncio
async def test_an_owner_who_lost_admin_loses_the_admin_socket_of_an_admin_key():
    resolver = Resolver()
    resolver.set("k1", {ApiScope.ADMIN}, account_type=AccountType.ADMIN)
    registry = _tracked_registry(resolver)
    socket = FakeSocket()
    registry._register(socket, _grant("k1"), ADMIN, was_admin=True)
    await _settle()
    assert socket.closed == []

    resolver.set("k1", {ApiScope.ADMIN}, account_type=AccountType.USER)
    registry.sweep()
    await _settle()

    assert socket.closed == [(KEY_CLOSE_CODE, NARROWED_REASON)]


@pytest.mark.asyncio
async def test_a_key_revoked_between_authenticating_and_registering_is_closed_on_registration():
    registry = _tracked_registry(Resolver())
    socket = FakeSocket()

    registry._register(socket, _grant("gone"), READ)
    await _settle()

    assert socket.closed == [(KEY_CLOSE_CODE, REVOKED_REASON)]


@pytest.mark.asyncio
async def test_a_key_moved_to_an_earlier_expiry_closes_at_the_sweep():
    resolver = Resolver()
    resolver.set("k1", {ApiScope.READ}, dt_iso(now_utc() + timedelta(days=30)))
    registry = _tracked_registry(resolver)
    socket = FakeSocket()
    registry._register(socket, _grant("k1", dt_iso(now_utc() + timedelta(days=30))), READ)
    await _settle()

    resolver.set("k1", {ApiScope.READ}, dt_iso(now_utc() - timedelta(seconds=1)))
    registry.sweep()
    await _settle()

    assert socket.closed == [(KEY_CLOSE_CODE, EXPIRED_REASON)]


@pytest.mark.asyncio
async def test_the_shared_registry_rechecks_a_key_when_it_is_changed():
    resolver = Resolver()
    resolver.set("changed", {ApiScope.READ})
    key_sockets.set_resolver(resolver)
    socket = FakeSocket()
    try:
        key_sockets._register(socket, _grant("changed"), ADMIN)
        await _settle()
        assert socket.closed != []
    finally:
        key_sockets.set_resolver(None)
        key_sockets._unregister(socket)

    other = FakeSocket()
    resolver.set("changed2", {ApiScope.READ})
    key_sockets.set_resolver(resolver)
    try:
        key_sockets._register(other, _grant("changed2"), READ)
        await _settle()
        resolver.set("changed2", set())
        key_revocation.notify_changed("changed2")
        await _settle()
        assert other.closed == [(KEY_CLOSE_CODE, NARROWED_REASON)]
    finally:
        key_sockets.set_resolver(None)
        key_sockets._unregister(other)


def test_track_is_the_only_public_way_to_follow_a_socket():
    public = {name for name in dir(KeySocketRegistry) if not name.startswith("_")}

    assert {"register", "attach", "unregister"}.isdisjoint(public)
    assert "track" in public
