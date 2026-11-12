import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.bootstrap.app import announce_claim_code
from src.platform.security.claim_token import ClaimTokenStore
from src.platform.settings.settings import Settings


@pytest.fixture
def store(tmp_path):
    settings = Mock(spec=Settings)
    settings.storage_file.side_effect = lambda name: tmp_path / name
    return ClaimTokenStore(settings)


def _container(store, claimed):
    return SimpleNamespace(
        instance_claim_repository=SimpleNamespace(is_claimed=lambda: claimed),
        claim_token_store=store,
    )


def test_an_unclaimed_instance_prints_the_code_on_its_own_line(store, capsys):
    assert announce_claim_code(_container(store, claimed=False)) is True

    lines = capsys.readouterr().out.splitlines()
    code = store.ensure_token()
    assert f"Claim code: {code}" in lines
    assert any("no owner yet" in line for line in lines)
    assert any("stops working once the owner exists" in line for line in lines)


def test_the_same_code_is_printed_on_every_start_while_unclaimed(store, capsys):
    container = _container(store, claimed=False)

    announce_claim_code(container)
    first = capsys.readouterr().out
    announce_claim_code(container)

    assert capsys.readouterr().out == first


def test_a_claimed_instance_prints_nothing(store, capsys):
    store.ensure_token()

    assert announce_claim_code(_container(store, claimed=True)) is False
    assert capsys.readouterr().out == ""


def test_the_code_never_reaches_a_log_record(store, capsys, caplog):
    with caplog.at_level(logging.DEBUG):
        announce_claim_code(_container(store, claimed=False))

    code = store.ensure_token()
    assert code not in caplog.text
    assert all(code not in record.getMessage() for record in caplog.records)
    assert code in capsys.readouterr().out


def test_no_storage_folder_prints_nothing(capsys):
    broken = ClaimTokenStore(Mock(spec=Settings, storage_file=Mock(side_effect=OSError("no storage"))))

    assert announce_claim_code(_container(broken, claimed=False)) is False
    assert capsys.readouterr().out == ""
