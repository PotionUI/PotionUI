from src.platform.security.api_scopes import ApiScope
import pytest

from src.platform.websocket.tickets import (
    TicketStoreFull,
    MAX_TICKETS,
    MAX_TICKETS_PER_CALLER,
    TICKET_TTL_SECONDS,
    WsTicketStore,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _store():
    clock = Clock()
    return WsTicketStore(clock), clock


def test_a_ticket_opens_its_socket_once():
    store, _ = _store()
    ticket = store.issue("/ws/generation", "u1")

    assert store.redeem(ticket, "/ws/generation") is not None
    assert store.redeem(ticket, "/ws/generation") is None


def test_a_ticket_is_43_url_safe_characters():
    store, _ = _store()
    ticket = store.issue("/ws/generation", "u1")

    assert len(ticket) == 43
    assert all(char.isalnum() or char in "-_" for char in ticket)


def test_a_ticket_expires_after_thirty_seconds():
    store, clock = _store()
    ticket = store.issue("/ws/generation", "u1")
    clock.now += TICKET_TTL_SECONDS

    assert store.redeem(ticket, "/ws/generation") is None


def test_a_ticket_is_good_just_before_it_expires():
    store, clock = _store()
    ticket = store.issue("/ws/generation", "u1")
    clock.now += TICKET_TTL_SECONDS - 0.5

    assert store.redeem(ticket, "/ws/generation") is not None


def test_a_ticket_for_one_socket_is_refused_on_another_and_is_spent_by_the_attempt():
    store, _ = _store()
    ticket = store.issue("/ws/notifications", "u1")

    assert store.redeem(ticket, "/ws/generation") is None
    assert store.redeem(ticket, "/ws/notifications") is None


def test_a_ticket_carries_the_caller_it_was_issued_to():
    store, _ = _store()
    session_ticket = store.issue("/ws/generation", "u1")
    key_ticket = store.issue("/ws/generation", "u2", "key-1", frozenset({ApiScope.READ}))

    session = store.redeem(session_ticket, "/ws/generation")
    key = store.redeem(key_ticket, "/ws/generation")

    assert (session.user_id, session.key_id, session.scopes) == ("u1", None, frozenset())
    assert (key.user_id, key.key_id, key.scopes) == ("u2", "key-1", frozenset({ApiScope.READ}))


def test_an_unknown_or_empty_ticket_is_refused():
    store, _ = _store()

    assert store.redeem("nope", "/ws/generation") is None
    assert store.redeem("", "/ws/generation") is None
    assert store.redeem("x" * 5000, "/ws/generation") is None


def test_the_store_holds_no_plaintext_ticket():
    store, _ = _store()
    ticket = store.issue("/ws/generation", "u1")

    assert ticket not in store._entries


def test_expired_tickets_are_dropped_when_the_next_one_is_issued():
    store, clock = _store()
    for _ in range(5):
        store.issue("/ws/generation", "u1")
    clock.now += TICKET_TTL_SECONDS + 1

    store.issue("/ws/generation", "u2")

    assert store.size() == 1


def test_one_user_cannot_hold_more_than_the_per_user_cap_and_loses_the_oldest():
    store, _ = _store()
    first = store.issue("/ws/generation", "u1")
    for _ in range(MAX_TICKETS_PER_CALLER):
        store.issue("/ws/generation", "u1")

    assert store.size() == MAX_TICKETS_PER_CALLER
    assert store.redeem(first, "/ws/generation") is None


def test_a_keys_flood_leaves_its_owners_session_tickets_alone():
    store, _ = _store()
    session = store.issue("/ws/generation", "u1")
    for _ in range(MAX_TICKETS_PER_CALLER * 3):
        store.issue("/ws/generation", "u1", "key-1", frozenset({ApiScope.READ}))

    assert store.redeem(session, "/ws/generation") is not None


def test_one_keys_flood_leaves_another_keys_tickets_alone():
    store, _ = _store()
    other = store.issue("/ws/generation", "u1", "key-2", frozenset({ApiScope.READ}))
    for _ in range(MAX_TICKETS_PER_CALLER * 3):
        store.issue("/ws/generation", "u1", "key-1", frozenset({ApiScope.READ}))

    assert store.redeem(other, "/ws/generation") is not None


def test_one_users_flood_leaves_another_users_ticket_alone():
    store, _ = _store()
    other = store.issue("/ws/generation", "u2")
    for _ in range(MAX_TICKETS_PER_CALLER * 3):
        store.issue("/ws/generation", "u1")

    assert store.redeem(other, "/ws/generation") is not None


def test_a_full_store_refuses_a_new_ticket_instead_of_evicting_anyone():
    store, _ = _store()
    first = store.issue("/ws/generation", "user-0")
    for index in range(1, MAX_TICKETS):
        store.issue("/ws/generation", f"user-{index}")

    with pytest.raises(TicketStoreFull):
        store.issue("/ws/generation", "latecomer")

    assert store.size() == MAX_TICKETS
    assert store.redeem(first, "/ws/generation") is not None
