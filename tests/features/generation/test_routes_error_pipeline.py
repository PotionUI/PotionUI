from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from src.features.generation.exceptions import (
    GenerationDeleteFailedException,
    GenerationNotFoundException,
    InvalidDateFilterException,
    InvalidTagException,
)
from src.features.generation.history_filter import HistoryFilter
from src.features.generation.history_executor import HistoryExecutorSaturated
from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.routes import GenerationController
from src.features.generation.run_report_recorder import RunReportRecorder
from src.platform.security.user import AccountType

LEAKY = "could not open /srv/data/potionui.db"


def _user(account_type):
    return Mock(id="u1", account_type=account_type)


def _controller(error):
    facade = Mock(spec=GenerationHistoryFacade)
    facade.get_by_id_async = AsyncMock(side_effect=error)
    return GenerationController(Mock(), facade, Mock(), Mock(spec=RunReportRecorder))


async def test_an_unexpected_failure_is_a_400_that_hides_the_text_from_a_non_admin():
    controller = _controller(RuntimeError(LEAKY))

    with pytest.raises(HTTPException) as caught:
        await controller.get_generation_by_id("g1", _user(AccountType.USER))

    assert caught.value.status_code == 400
    assert caught.value.detail["error"] == "generation_fetch_failed"
    assert "potionui.db" not in caught.value.detail["message"]


async def test_an_unexpected_failure_shows_an_admin_the_scrubbed_text():
    controller = _controller(RuntimeError(LEAKY))

    with pytest.raises(HTTPException) as caught:
        await controller.get_generation_by_id("g1", _user(AccountType.ADMIN))

    assert "could not open" in caught.value.detail["message"]
    assert "/srv/data" not in caught.value.detail["message"]


async def test_a_missing_generation_is_a_404_with_its_own_message():
    controller = _controller(GenerationNotFoundException("Generation 'g1' not found"))

    with pytest.raises(HTTPException) as caught:
        await controller.get_generation_by_id("g1", _user(AccountType.USER))

    assert caught.value.status_code == 404
    assert caught.value.detail["error"] == "generation_not_found"
    assert caught.value.detail["message"] == "Generation 'g1' not found"


async def test_a_saturated_history_executor_is_a_503():
    controller = _controller(HistoryExecutorSaturated())

    with pytest.raises(HTTPException) as caught:
        await controller.get_generation_by_id("g1", _user(AccountType.USER))

    assert caught.value.status_code == 503
    assert caught.value.detail["error"] == "history_busy"


def _facade_controller(**facade_calls):
    facade = Mock(spec=GenerationHistoryFacade)
    for name, error in facade_calls.items():
        setattr(facade, name, (AsyncMock if name.endswith("_async") else Mock)(side_effect=error))
    return GenerationController(Mock(), facade, Mock(), Mock(spec=RunReportRecorder))


_CASES = [
    ("set_rating", ValueError("Rating must be between 0 and 5"), 400, "invalid_rating",
     lambda c, u: c.set_generation_rating("g1", 9, u)),
    ("set_rating", GenerationNotFoundException("gone"), 404, "generation_not_found",
     lambda c, u: c.set_generation_rating("g1", 3, u)),
    ("update_tags", InvalidTagException("bad tag"), 400, "invalid_tag",
     lambda c, u: c.update_generation_tags("g1", ["x"], u)),
    ("update_tags", GenerationNotFoundException("gone"), 404, "generation_not_found",
     lambda c, u: c.update_generation_tags("g1", ["x"], u)),
    ("update_tags", RuntimeError(LEAKY), 400, "update_tags_failed",
     lambda c, u: c.update_generation_tags("g1", ["x"], u)),
    ("delete", GenerationDeleteFailedException("blocked"), 400, "delete_failed",
     lambda c, u: c.delete_generation_history("g1", u)),
    ("delete", GenerationNotFoundException("gone"), 404, "generation_not_found",
     lambda c, u: c.delete_generation_history("g1", u)),
    ("get_history_version_async", HistoryExecutorSaturated(), 503, "history_busy",
     lambda c, u: c.get_history_version(u)),
    ("get_history_async", InvalidDateFilterException("bad date"), 400, "invalid_date_format",
     lambda c, u: c.get_generation_history(u, HistoryFilter())),
]


@pytest.mark.parametrize("facade_call, error, status, code, invoke", _CASES)
async def test_a_handler_maps_its_exception_to_the_documented_status_and_code(
    facade_call, error, status, code, invoke
):
    controller = _facade_controller(**{facade_call: error})

    with pytest.raises(HTTPException) as caught:
        await invoke(controller, _user(AccountType.USER))

    assert caught.value.status_code == status
    assert caught.value.detail["error"] == code
    assert "potionui.db" not in caught.value.detail["message"]


async def test_a_validation_error_keeps_its_user_facing_message():
    controller = _facade_controller(set_rating=ValueError("Rating must be between 0 and 5"))

    with pytest.raises(HTTPException) as caught:
        await controller.set_generation_rating("g1", 9, _user(AccountType.USER))

    assert caught.value.detail["message"] == "Rating must be between 0 and 5"



def _blocked(reason, details):
    error = GenerationDeleteFailedException(reason)
    if details is not None:
        error.block_details = details
    return error


@pytest.mark.parametrize("facade_call, invoke", [
    ("delete", lambda c, u: c.delete_generation_history("g1", u)),
    ("bulk_delete", lambda c, u: c.bulk_delete_generations(["g1", "g2"], u)),
])
async def test_a_blocked_delete_carries_the_block_details_next_to_its_message(facade_call, invoke):
    details = {"blocking_runs": [{"run_id": "r1", "number": 3}]}
    controller = _facade_controller(**{facade_call: _blocked("Brew 3 still needs this", details)})

    with pytest.raises(HTTPException) as caught:
        await invoke(controller, _user(AccountType.USER))

    assert caught.value.status_code == 400
    assert caught.value.detail["message"] == "Brew 3 still needs this"
    assert caught.value.detail["blocking_runs"] == details["blocking_runs"]


async def test_a_blocked_delete_without_details_keeps_the_plain_shape():
    controller = _facade_controller(delete=_blocked("blocked", None))

    with pytest.raises(HTTPException) as caught:
        await controller.delete_generation_history("g1", _user(AccountType.USER))

    assert set(caught.value.detail) == {"success", "error", "message", "code"}
