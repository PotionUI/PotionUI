import asyncio
from dataclasses import replace

import pytest

from src.platform.security.api_scopes import ApiScope
from src.platform.security.principal import KeyGrant
from src.platform.security.user import AccountType
from tests.features.elixirs.conftest import make_user
from tests.features.elixirs.test_coordinator import coordinator, media_to_enhance, start


def admin_session():
    return replace(make_user("alice"), account_type=AccountType.ADMIN)


def key_without_admin():
    grant = KeyGrant(key_id="k1", user_id="alice", scopes=frozenset({ApiScope.GENERATE}), display_prefix="pui_aaaaaaaa")
    return replace(admin_session(), key=grant, account_type=AccountType.USER)


def key_with_admin():
    grant = KeyGrant(key_id="k2", user_id="alice", scopes=frozenset({ApiScope.GENERATE, ApiScope.ADMIN}), display_prefix="pui_bbbbbbbb")
    return replace(admin_session(), key=grant)


def run_to_completion_step(coordinator):
    asyncio.run(coordinator.pump())


@pytest.mark.parametrize("label,initiator,expected", [
    ("an admin's browser session", admin_session(), True),
    ("a key that holds admin", key_with_admin(), True),
    ("a key without admin on an admin's account", key_without_admin(), False),
    ("a plain user", make_user("alice"), False),
])
def test_the_steps_of_a_brew_carry_the_authority_of_whoever_started_it(coordinator, port, contracts, label, initiator, expected):
    port.users["alice"] = admin_session()

    run = start(coordinator, initiator, media_to_enhance(("p1", "p2")), contracts)
    run_to_completion_step(coordinator)

    assert port.authority == [expected, expected], label
    assert coordinator.store.get_run(run["id"])["graph"]["acts_as_admin"] is expected


def test_a_run_stored_before_authority_was_recorded_acts_as_its_owner_did(coordinator, port, contracts):
    port.users["alice"] = admin_session()
    run = start(coordinator, admin_session(), media_to_enhance(("p1",)), contracts)
    with coordinator.store.transaction() as cursor:
        stored = coordinator.store.load_run(cursor, run["id"])
        graph = dict(stored["graph"])
        graph.pop("acts_as_admin")
        coordinator.store.save_run(cursor, run["id"], {"graph": graph})

    run_to_completion_step(coordinator)

    assert port.authority == [True]


def test_a_legacy_run_of_a_plain_owner_stays_without_authority(coordinator, port, contracts):
    run = start(coordinator, make_user("alice"), media_to_enhance(("p1",)), contracts)
    with coordinator.store.transaction() as cursor:
        stored = coordinator.store.load_run(cursor, run["id"])
        graph = dict(stored["graph"])
        graph.pop("acts_as_admin")
        coordinator.store.save_run(cursor, run["id"], {"graph": graph})

    run_to_completion_step(coordinator)

    assert port.authority == [False]


def test_a_retry_is_judged_by_whoever_retries(coordinator, port, contracts):
    port.users["alice"] = admin_session()
    run = start(coordinator, admin_session(), media_to_enhance(("p1",)), contracts)
    run_to_completion_step(coordinator)
    port.fail("g1")
    run_to_completion_step(coordinator)

    coordinator.retry_failed(key_without_admin(), run["id"], "sharpen")
    run_to_completion_step(coordinator)
    port.fail("g2")
    run_to_completion_step(coordinator)
    coordinator.retry_failed(admin_session(), run["id"], "sharpen")
    run_to_completion_step(coordinator)

    assert port.authority == [True, False, True]
