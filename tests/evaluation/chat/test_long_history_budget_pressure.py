"""Proves the `long_history_latest_question` scenario's history fixture
ACTUALLY exceeds its stated compact budget, through the SAME accounting the
app uses for a real turn (``src.features.llm.context_budget.enforce_budget``)
— not a prose claim ("long history") taken on faith.

The `latest_question_reflected` transcript-level check in
``test_evaluator.py`` proves the model's FINAL ANSWER talks about the latest
question; this test proves something upstream of that — that the fixture's
raw history genuinely trims under real budget pressure and that the current
turn's user message survives that trim, exactly as
``context_budget._protected_unit_count`` guarantees for a real turn.
"""

from src.features.llm import context_budget
from tests.evaluation.chat import fixtures


def _pressure_fixture():
    scenario = fixtures.load_all_scenarios()["long_history_latest_question"]
    return scenario["context"]["budget_pressure"]


class TestBudgetPressureIsReal:
    def test_stated_capacity_assumption_is_explicit(self):
        pressure = _pressure_fixture()
        assert isinstance(pressure["capacity_tokens"], int) and pressure["capacity_tokens"] > 0
        assert pressure["capacity_source"], "the fixture must state WHERE its capacity assumption comes from"
        assert isinstance(pressure["reserve_tokens"], int)
        assert len(pressure["raw_history"]) > 10, "needs enough turns to meaningfully exceed a compact budget"

    def test_history_exceeds_compact_budget_and_drops_older_turns(self):
        pressure = _pressure_fixture()
        outcome = context_budget.enforce_budget(
            capacity_tokens=pressure["capacity_tokens"],
            capacity_source="test",
            reserve_tokens=pressure["reserve_tokens"],
            system_message=None,
            messages=pressure["raw_history"],
            tool_schemas=None,
        )
        ledger = outcome.ledger
        assert ledger["messages_dropped"] > 0, (
            "the fixture must actually exceed the stated compact budget - if nothing was "
            "dropped this isn't pressure, it's just a long prose label"
        )
        assert ledger["messages_sent"] < ledger["messages_total"]

    def test_latest_question_survives_the_trim(self):
        pressure = _pressure_fixture()
        history = pressure["raw_history"]
        latest_question = history[-1]["content"]
        assert "upscaler" in latest_question and "2x" in latest_question

        outcome = context_budget.enforce_budget(
            capacity_tokens=pressure["capacity_tokens"],
            capacity_source="test",
            reserve_tokens=pressure["reserve_tokens"],
            system_message=None,
            messages=history,
            tool_schemas=None,
        )
        kept_contents = [m["content"] for m in outcome.messages]
        assert latest_question in kept_contents, "the current turn's own question must never be trimmed away"
        # It must survive specifically because it's protected, not by luck of
        # fitting anyway - prove real pressure forced something else out.
        assert len(kept_contents) < len(history)

    def test_a_much_larger_capacity_would_not_need_to_drop_anything(self):
        """Sanity control: the SAME history under a generous capacity keeps
        everything - the dropping above is genuinely caused by the stated
        compact capacity, not some unrelated bug always dropping messages."""
        pressure = _pressure_fixture()
        outcome = context_budget.enforce_budget(
            capacity_tokens=1_000_000,
            capacity_source="test",
            reserve_tokens=pressure["reserve_tokens"],
            system_message=None,
            messages=pressure["raw_history"],
            tool_schemas=None,
        )
        assert outcome.ledger["messages_dropped"] == 0
