from datetime import timedelta

from src.features.api_keys.operations import keys as operations
from src.features.api_keys.records import ApiKey
from src.platform.database.rows import dt_column
from src.platform.security.api_scopes import ApiScope as S


def legacy(owner_id, key_id="legacy", **overrides):
    values = dict(
        id=key_id, user_id=owner_id, name="laptop", secret_hash=f"hash-{key_id}", display_prefix="pui_mcp_Ab3_",
        last_four=None, scopes=frozenset({S.MCP}), created_at="2026-05-01T00:00:00+00:00",
        last_used_at="2026-05-02T14:20:11.123456+00:00",
    )
    values.update(overrides)
    return ApiKey(**values)


def test_a_new_key_and_a_copied_one_sort_by_creation_and_stay_active(stack):
    owner = stack.user()
    stack.keys.create(legacy(owner.id))
    new, _ = operations.mint(stack.collaborators, owner, "fresh", ["read"], 30, True)

    listed = operations.list_for_user(stack.collaborators, owner.id)

    assert [key.id for key in listed] == [new.id, "legacy"]
    assert all(key.status() == "active" for key in listed)


def test_a_copied_revoked_row_is_revoked_and_one_with_an_old_expiry_is_expired(stack):
    owner = stack.user()
    stack.keys.create(legacy(owner.id, "gone", revoked_at="2026-06-01T00:00:00+00:00", revoked_by=owner.id))
    stack.keys.create(legacy(owner.id, "old", expires_at="2026-06-01T00:00:00.5+00:00"))

    assert stack.keys.get_by_id("gone").status() == "revoked"
    assert stack.keys.get_by_id("old").status() == "expired"


def test_the_admin_status_filters_agree_with_the_python_status_for_mixed_rows(stack):
    owner = stack.user()
    stack.keys.create(legacy(owner.id, "a"))
    stack.keys.create(legacy(owner.id, "r", revoked_at="2026-06-01T00:00:00+00:00", revoked_by=owner.id))
    new, _ = operations.mint(stack.collaborators, owner, "fresh", ["read"], 30, True)
    now = dt_column(stack.keys.get_by_id(new.id).created_at).isoformat()

    def ids(status):
        return sorted(key.id for key in stack.keys.list_page(status, None, now, 50, 0)[0])

    assert ids("active") == sorted(["a", new.id])
    assert ids("revoked") == ["r"]
    assert ids("expired") == []


def test_the_expiry_is_exactly_the_requested_days_after_creation(stack):
    owner = stack.user()
    key, _ = operations.mint(stack.collaborators, owner, "k", ["read"], 90, True)

    assert dt_column(key.expires_at) - dt_column(key.created_at) == timedelta(days=90)


def test_a_copied_row_with_a_microsecond_last_use_is_not_rewritten_within_that_hour(stack):
    owner = stack.user()
    stack.keys.create(legacy(owner.id))

    assert not stack.keys.touch_last_used("legacy", "2026-05-02T14:00:00+00:00", "mcp")
    assert stack.keys.touch_last_used("legacy", "2026-05-02T15:00:00+00:00", "mcp")
