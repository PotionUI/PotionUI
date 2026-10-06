from types import SimpleNamespace

import pytest

from src.platform.security import current_user, media_session


def _auth(secret):
    return SimpleNamespace(tokens=SimpleNamespace(_config=SimpleNamespace(secret_key=secret)))


def _request(cookies):
    return SimpleNamespace(cookies=cookies)


@pytest.fixture
def use_secret(monkeypatch):
    def apply(secret):
        monkeypatch.setattr(current_user, "get_auth", lambda: _auth(secret))
        return media_session.media_cookie_name()

    return apply


def test_two_instances_with_different_keys_use_different_cookie_names(use_secret):
    dev = use_secret("dev-secret")
    prod = use_secret("prod-secret")
    assert dev != prod
    assert dev.startswith("potionui_media_") and prod.startswith("potionui_media_")


def test_the_name_is_stable_for_one_key(use_secret):
    assert use_secret("same") == use_secret("same")


def test_a_cookie_written_by_another_instance_is_not_read(use_secret):
    prod_name = use_secret("prod-secret")
    dev_name = use_secret("dev-secret")
    request = _request({prod_name: "prod-token"})
    assert media_session.media_cookie_token(request) is None
    request = _request({dev_name: "dev-token", prod_name: "prod-token"})
    assert media_session.media_cookie_token(request) == "dev-token"


def test_the_legacy_shared_name_is_ignored(use_secret):
    use_secret("dev-secret")
    assert media_session.media_cookie_token(_request({"potionui_media": "other"})) is None


def test_two_instances_sharing_a_key_still_use_different_cookie_names(use_secret, monkeypatch):
    monkeypatch.setattr(media_session, "_instance_location", lambda: "/srv/dev/storage/db.sqlite")
    dev = use_secret("shared-secret")
    monkeypatch.setattr(media_session, "_instance_location", lambda: "/srv/prod/storage/db.sqlite")
    prod = use_secret("shared-secret")
    assert dev != prod
    assert media_session.media_cookie_token(_request({dev: "dev-token"})) is None
