import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.bootstrap.errors import register_error_handlers
from src.features.plans.errors import LimitExceeded
from src.platform.http.base_controller import BaseController
from src.platform.http.errors import NotFoundError
from src.platform.http.error_pipeline import Fail, guarded, raw_detail, soft_guarded
from src.platform.security.failure_text import failure_detail
from src.platform.security.user import AccountType


class Missing(Exception):
    pass


class User:
    def __init__(self, admin):
        self.account_type = AccountType.ADMIN if admin else AccountType.USER


TABLE = {Missing: Fail("missing", "Gone", status=404), KeyError: Fail("key_error")}
FALLBACK = Fail("failed", "Failed", detail=failure_detail)


class Stub(BaseController):
    @guarded(TABLE, FALLBACK, admin="user")
    async def run(self, raised, user):
        raise raised

    @guarded(TABLE, FALLBACK, admin="user")
    def run_sync(self, raised, user):
        raise raised

    @guarded(TABLE)
    async def no_fallback(self, raised):
        raise raised

    @guarded({}, Fail("failed", "Failed", detail=raw_detail))
    async def raw(self, raised):
        raise raised

    @guarded({}, FALLBACK)
    async def forbidden(self):
        raise HTTPException(status_code=403, detail="no")

    @guarded({}, FALLBACK)
    def forbidden_sync(self):
        raise HTTPException(status_code=403, detail="no")

    @guarded(TABLE, FALLBACK, admin="user")
    async def ok(self, user):
        return {"ok": True}


def refusal():
    return LimitExceeded([{
        "kind": "storage_bytes", "code": "storage_quota_exceeded", "label": "Storage", "format": "bytes",
        "used": 1, "limit": 1, "percent": 100.0, "resets_at": None, "message": "Full.",
    }], "upload", "")


def client():
    app = FastAPI()
    register_error_handlers(app)
    stub = Stub()

    @app.get("/soft")
    async def soft():
        return await stub.run(KeyError("k"), User(False))

    @app.get("/hard")
    async def hard():
        return await stub.run(Missing("x"), User(False))

    @app.get("/leak/{role}")
    async def leak(role: str):
        return await stub.run(RuntimeError("db password=hunter2"), User(role == "admin"))

    @app.get("/sync")
    def sync():
        return stub.run_sync(RuntimeError("db password=hunter2"), User(True))

    @app.get("/limit")
    async def limit():
        return await stub.run(refusal(), User(True))

    @app.get("/ok")
    async def ok():
        return await stub.ok(User(False))

    return TestClient(app, raise_server_exceptions=False)


def test_a_table_entry_without_status_is_a_200_soft_envelope():
    response = client().get("/soft")

    assert response.status_code == 200
    assert response.json() == {"success": False, "data": None, "message": "'k'", "error": "key_error"}


def test_a_table_entry_with_status_raises_that_status_code_and_message():
    response = client().get("/hard")

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "missing"
    assert response.json()["detail"]["message"] == "Gone"


def test_the_fallback_hides_raw_text_from_non_admins():
    body = client().get("/leak/user").json()

    assert body["error"] == "failed"
    assert "db password=hunter2" not in body["message"]


def test_the_fallback_shows_raw_text_to_admins():
    assert "db password=hunter2" in client().get("/leak/admin").json()["message"]


def test_a_sync_handler_binds_admin_through_the_signature():
    response = client().get("/sync")

    assert response.status_code == 200
    assert "db password=hunter2" in response.json()["message"]


def test_a_successful_call_returns_its_value():
    assert client().get("/ok").json() == {"ok": True}


async def test_without_a_fallback_an_unmapped_exception_propagates():
    with pytest.raises(RuntimeError):
        await Stub().no_fallback(RuntimeError("x"))


async def test_a_raw_detail_fallback_is_the_exception_text():
    response = await Stub().raw(RuntimeError("boom"))

    assert response.message == "Failed: boom"


def test_a_pass_through_bypasses_the_table_and_the_global_handler_answers_403():
    response = client().get("/limit")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "storage_quota_exceeded"


async def test_an_http_exception_passes_through_an_async_fallback():
    with pytest.raises(HTTPException) as caught:
        await Stub().forbidden()

    assert caught.value.status_code == 403
    assert caught.value.detail == "no"


def test_an_http_exception_passes_through_a_sync_fallback():
    with pytest.raises(HTTPException) as caught:
        Stub().forbidden_sync()

    assert caught.value.status_code == 403


class SoftStub(BaseController):
    @soft_guarded("read_failed", "Failed to read", admin="user")
    async def read(self, raised, user):
        raise raised

    @guarded({}, FALLBACK, soft=True)
    async def soft_table(self, raised):
        raise raised

    @guarded({}, FALLBACK)
    async def hard_table(self, raised):
        raise raised


async def test_a_soft_guard_keeps_the_envelope_for_a_typed_error():
    response = await SoftStub().read(NotFoundError("thing_missing", "No such thing"), User(False))

    assert (response.success, response.error, response.message) == (False, "thing_missing", "No such thing")


async def test_a_hard_guard_still_raises_the_typed_error_status():
    with pytest.raises(HTTPException) as caught:
        await SoftStub().hard_table(NotFoundError("thing_missing", "No such thing"))

    assert caught.value.status_code == 404
    assert caught.value.detail["error"] == "thing_missing"


async def test_a_soft_guard_returns_value_error_text_verbatim():
    response = await SoftStub().read(ValueError("Name is required"), User(False))

    assert (response.error, response.message) == ("read_failed", "Name is required")


async def test_a_soft_guard_hides_unexpected_text_from_users_and_shows_admins():
    regular = await SoftStub().read(RuntimeError("secret at /srv/app/x"), User(False))
    admin = await SoftStub().read(RuntimeError("secret at /srv/app/x"), User(True))

    assert "secret" not in regular.message
    assert regular.message.startswith("Failed to read")
    assert "secret at <server path>" in admin.message


async def test_the_soft_flag_applies_to_plain_guarded_too():
    response = await SoftStub().soft_table(NotFoundError("thing_missing", "No such thing"))

    assert response.success is False



class Held(Exception):
    pass


class Extras(BaseController):
    @guarded({Held: Fail("held", "Held", status=400, extra=lambda exc: getattr(exc, "extras", None))})
    async def run(self, raised):
        raise raised


async def test_extra_fields_are_merged_into_the_error_detail():
    raised = Held()
    raised.extras = {"blocking": [1, 2], "message": "ignored"}

    with pytest.raises(HTTPException) as caught:
        await Extras().run(raised)

    assert caught.value.detail["blocking"] == [1, 2]
    assert caught.value.detail["error"] == "held"
    assert caught.value.detail["message"] == "Held"


async def test_an_extra_that_returns_nothing_changes_nothing():
    with pytest.raises(HTTPException) as caught:
        await Extras().run(Held())

    assert set(caught.value.detail) == {"success", "error", "message", "code"}
    assert caught.value.detail["message"] == "Held"
