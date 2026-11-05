import asyncio
import logging
import threading
from unittest.mock import patch

import pytest

from src.features.models.provider_info import ProviderInfoFetcher
from src.features.models.records import Model
from src.features.models.repository import model_repo
from tests.features.models.fakes import OpenPlugins

SHA_A = "a" * 64
SHA_B = "b" * 64


class FakeDigests:
    def __init__(self, digests):
        self.digests = dict(digests)
        self.calls = []
        self.threads = []

    def ensure_digest(self, model_id, timeout=None):
        self.calls.append(model_id)
        self.threads.append(threading.get_ident())
        return self.digests.get(model_id)


class FakeProviders:
    def __init__(self):
        self.lookups = []

    async def get_model_by_hash(self, provider, sha256, raise_errors=False):
        self.lookups.append(sha256)
        return None


def _model(name, sha256=None, is_directory=False):
    return model_repo.create(Model(filename=name, model_type="lora", sha256=sha256, is_directory=is_directory))


def _fetch(fetcher, providers, model_ids):
    with patch("src.features.providers.registry.get_provider_registry", return_value=providers):
        return asyncio.run(fetcher.run_provider_fetch("civitai", model_ids))


@pytest.fixture
def fetcher_for():
    def build(digests):
        return ProviderInfoFetcher(model_repo, OpenPlugins(), digests=digests)

    return build


def test_a_model_without_a_digest_is_hashed_first_and_then_looked_up_by_it(mock_db, fetcher_for):
    pending = _model("a.safetensors")
    ready = _model("b.safetensors", sha256=SHA_B)
    digests = FakeDigests({pending.id: SHA_A})
    providers = FakeProviders()

    result = _fetch(fetcher_for(digests), providers, [pending.id, ready.id])

    assert digests.calls == [pending.id]
    assert providers.lookups == [SHA_A, SHA_B]
    assert result["successful"] == 0 and result["failed"] == 2


def test_the_hashing_is_reported_by_position_and_runs_off_the_event_loop(mock_db, fetcher_for, caplog):
    first = _model("a.safetensors")
    second = _model("b.safetensors")
    digests = FakeDigests({first.id: SHA_A, second.id: SHA_B})

    with caplog.at_level(logging.INFO, logger="src.features.models.provider_info"):
        _fetch(fetcher_for(digests), FakeProviders(), [first.id, second.id])

    messages = [r.getMessage() for r in caplog.records if "Hashing" in r.getMessage()]
    assert messages == [
        "Hashing 1 of 2 before the civitai lookup: a.safetensors",
        "Hashing 2 of 2 before the civitai lookup: b.safetensors",
    ]
    assert threading.get_ident() not in digests.threads


def test_the_total_in_the_progress_message_leaves_out_models_that_are_not_hashed(mock_db, fetcher_for, caplog):
    directory = _model("folder", is_directory=True)
    ready = _model("b.safetensors", sha256=SHA_B)
    pending = _model("c.safetensors")
    digests = FakeDigests({pending.id: SHA_A})

    with caplog.at_level(logging.INFO, logger="src.features.models.provider_info"):
        _fetch(fetcher_for(digests), FakeProviders(), [directory.id, ready.id, pending.id])

    messages = [r.getMessage() for r in caplog.records if "Hashing" in r.getMessage()]
    assert messages == ["Hashing 1 of 1 before the civitai lookup: c.safetensors"]


def test_a_file_that_cannot_be_hashed_is_a_reported_failure_and_the_rest_carry_on(mock_db, fetcher_for):
    unreadable = _model("a.safetensors")
    readable = _model("b.safetensors")
    digests = FakeDigests({readable.id: SHA_B})
    providers = FakeProviders()

    result = _fetch(fetcher_for(digests), providers, [unreadable.id, readable.id])

    assert providers.lookups == [SHA_B]
    assert result["errors"][0] == {
        "model_id": unreadable.id,
        "reason": "The file could not be read, so no SHA256 hash could be made for it",
        "code": "no_hash",
    }
    assert result["failed"] == 2


def test_a_directory_model_is_skipped_without_hashing_it(mock_db, fetcher_for):
    directory = _model("folder", is_directory=True)
    digests = FakeDigests({})

    result = _fetch(fetcher_for(digests), FakeProviders(), [directory.id])

    assert digests.calls == []
    assert (result["successful"], result["failed"]) == (0, 0)


def test_without_a_digest_service_a_model_with_no_digest_is_reported_as_unreadable(mock_db):
    pending = _model("a.safetensors")

    result = _fetch(ProviderInfoFetcher(model_repo, OpenPlugins()), FakeProviders(), [pending.id])

    assert result["errors"] == [
        {
            "model_id": pending.id,
            "reason": "The file could not be read, so no SHA256 hash could be made for it",
            "code": "no_hash",
        }
    ]


class MismatchProviders:
    async def get_model_by_hash(self, provider, sha256, raise_errors=False):
        from src.features.providers import ProviderHashMismatchError

        if sha256 == SHA_A:
            raise ProviderHashMismatchError("CivitAI answered with a different file")
        return None


def test_each_failed_lookup_reports_a_typed_code_next_to_its_reason(mock_db, fetcher_for):
    mismatched = _model("a.safetensors", sha256=SHA_A)
    missing = _model("b.safetensors", sha256=SHA_B)

    result = _fetch(fetcher_for(None), MismatchProviders(), [mismatched.id, missing.id])

    by_model = {e["model_id"]: e for e in result["errors"]}
    assert by_model[mismatched.id]["code"] == "hash_mismatch"
    assert by_model[mismatched.id]["reason"] == "CivitAI answered with a different file"
    assert by_model[missing.id]["code"] == "not_found"
    assert result["failed"] == 2
