"""Setup status, loopback detection, and the one-time claim token."""

from unittest.mock import Mock

import pytest

from src.platform.http.origin import is_loopback_host
from src.platform.security.claim_token import ClaimTokenManager, CLAIM_TOKEN_FILENAME
from src.features.setup import operations


# --- loopback detection ----------------------------------------------------

@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "127.0.0.5", "::ffff:127.0.0.1"])
def test_loopback_hosts(host):
    assert is_loopback_host(host) is True


@pytest.mark.parametrize("host", ["10.0.0.4", "192.168.1.2", "example.com", "", None, "::ffff:8.8.8.8"])
def test_non_loopback_hosts(host):
    assert is_loopback_host(host) is False


# --- claim token -----------------------------------------------------------

def _token_manager(tmp_path):
    settings = Mock()
    settings.get_file_storage_directory.return_value = str(tmp_path)
    return ClaimTokenManager(settings), settings


def test_ensure_token_persists_0600(tmp_path):
    import stat

    manager, _ = _token_manager(tmp_path)
    token = manager.ensure_token()

    assert token
    token_file = tmp_path / CLAIM_TOKEN_FILENAME
    assert token_file.exists()
    assert token_file.read_text().strip() == token
    mode = stat.S_IMODE(token_file.stat().st_mode)
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0  # no group/other access


def test_ensure_token_is_stable(tmp_path):
    manager, _ = _token_manager(tmp_path)
    first = manager.ensure_token()
    assert manager.ensure_token() == first  # does not regenerate on each call


def test_verify_and_clear(tmp_path):
    manager, _ = _token_manager(tmp_path)
    token = manager.ensure_token()

    assert manager.verify(token) is True
    assert manager.verify("nope") is False
    assert manager.verify(None) is False

    manager.clear()
    assert manager.exists() is False
    assert manager.verify(token) is False  # no token on disk -> nothing verifies


# --- setup status ----------------------------------------------------------

def _collaborators(claimed, policy="closed", token_exists=False):
    claim = Mock()
    claim.is_claimed.return_value = claimed
    settings = Mock()
    settings.get_setting.return_value = policy
    tokens = Mock()
    tokens.exists.return_value = token_exists
    return claim, tokens, settings


def test_status_unclaimed_loopback():
    claim, tokens, settings = _collaborators(claimed=False, token_exists=True)
    status = operations.status(claim, tokens, settings, is_loopback=True)
    assert status.needs_owner is True
    assert status.registration_open is True          # always open while unclaimed
    assert status.claim_requires_token is False       # loopback is trusted


def test_status_unclaimed_remote_with_token():
    claim, tokens, settings = _collaborators(claimed=False, token_exists=True)
    status = operations.status(claim, tokens, settings, is_loopback=False)
    assert status.needs_owner is True
    assert status.claim_requires_token is True


def test_status_claimed_closed():
    claim, tokens, settings = _collaborators(claimed=True, policy="closed")
    status = operations.status(claim, tokens, settings, is_loopback=True)
    assert status.needs_owner is False
    assert status.registration_open is False
    assert status.claim_requires_token is False


def test_status_claimed_open():
    claim, tokens, settings = _collaborators(claimed=True, policy="open")
    status = operations.status(claim, tokens, settings, is_loopback=False)
    assert status.needs_owner is False
    assert status.registration_open is True


def test_status_exposes_only_three_booleans():
    """The public payload must leak nothing about the host."""
    claim, tokens, settings = _collaborators(claimed=False)
    status = operations.status(claim, tokens, settings, is_loopback=True)
    assert set(status.model_dump().keys()) == {
        "needs_owner", "registration_open", "claim_requires_token"
    }
    assert all(isinstance(v, bool) for v in status.model_dump().values())
