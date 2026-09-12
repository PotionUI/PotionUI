"""Tests for the setup claim token store."""
import os
import stat
from unittest.mock import Mock

import pytest

from src.platform.security.claim_token import CLAIM_TOKEN_FILENAME, ClaimTokenStore
from src.platform.settings.settings import Settings


@pytest.fixture
def settings(tmp_path):
    m = Mock(spec=Settings)
    m.get_file_storage_directory.return_value = str(tmp_path)
    return m


@pytest.fixture
def store(settings):
    return ClaimTokenStore(settings)


class TestEnsureToken:
    def test_generates_and_persists_with_0600(self, store, settings, tmp_path):
        token = store.ensure_token()
        path = tmp_path / CLAIM_TOKEN_FILENAME
        assert token
        assert path.read_text().strip() == token
        # 0600 is a POSIX permission; Windows chmod only toggles the
        # read-only attribute, so there is no equivalent bit pattern to
        # assert there - existence and content are checked above regardless.
        if os.name != "nt":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_generation_is_stable_across_calls(self, store):
        assert store.ensure_token() == store.ensure_token()

    def test_generates_and_persists_without_fchmod(self, store, tmp_path, monkeypatch):
        """First boot still writes a usable, owner-only token file on a
        platform (Windows) with no os.fchmod - the persist path must fall
        back to chmod-by-path instead of raising AttributeError."""
        monkeypatch.delattr(os, "fchmod", raising=False)
        token = store.ensure_token()
        path = tmp_path / CLAIM_TOKEN_FILENAME
        assert token
        assert path.read_text().strip() == token
        # See test_generates_and_persists_with_0600: POSIX-only mode check.
        if os.name != "nt":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_no_storage_dir_returns_none(self):
        m = Mock(spec=Settings)
        m.get_file_storage_directory.return_value = ""
        assert ClaimTokenStore(m).ensure_token() is None


class TestVerifyAndClear:
    def test_verify_accepts_the_persisted_token(self, store):
        token = store.ensure_token()
        assert store.verify(token) is True

    def test_verify_rejects_wrong_token(self, store):
        store.ensure_token()
        assert store.verify("wrong") is False

    def test_verify_rejects_when_no_token_exists(self, store):
        assert store.verify("anything") is False

    def test_clear_removes_the_file(self, store, tmp_path):
        store.ensure_token()
        path = tmp_path / CLAIM_TOKEN_FILENAME
        assert path.exists()
        store.clear()
        assert not path.exists()
        assert store.exists() is False
