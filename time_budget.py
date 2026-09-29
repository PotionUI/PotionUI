import os
import time

import pytest

BUDGET_ENV = "POTIONUI_TEST_BUDGET_S"


class TestBudgetExceeded(AssertionError):
    __test__ = False


def read_budget(environ=None) -> float:
    raw = (os.environ if environ is None else environ).get(BUDGET_ENV, "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 else 0.0


def budget_message(nodeid: str, elapsed: float, budget: float) -> str:
    return (
        f"{nodeid} took {elapsed:.1f}s in its call phase, over the {budget:g}s per-test budget "
        f"({BUDGET_ENV}). Keep tests fast: use fakes, stubs and tiny tensors instead of real "
        f"model-sized work, or split the test."
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    budget = read_budget()
    if not budget:
        return (yield)
    started = time.perf_counter()
    result = yield
    elapsed = time.perf_counter() - started
    if elapsed > budget:
        raise TestBudgetExceeded(budget_message(item.nodeid, elapsed, budget))
    return result
