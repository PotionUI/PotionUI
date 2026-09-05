"""Proves the `long_history_latest_question` scenario's history fixture
ACTUALLY exceeds its stated compact budget, through the SAME accounting the
app uses for a real turn (``src.features.llm.context_budget.enforce_budget``)
— not a prose claim ("long history") taken on faith.

This is the same history that actually reaches the evaluated turn: the
scenario's own ``user_turns`` (sent as real turns by a live ``run``) and the
``.good`` transcript's message history (what ``replay`` scores the final
answer against) are BOTH derived from this padding — see
``evaluator.py``'s ``budget_pressure_observed`` check, which proves the same
thing through ``evaluate_transcript`` itself (via the real backend ledger for
a live capture, or a recompute here for a canned one); this file is the
direct, standalone proof that the recompute path's numbers are real.
"""

from src.features.llm import context_budget
from tests.evaluation.chat import fixtures


def _pressure_assumption():
    scenario = fixtures.load_all_scenarios()["long_history_latest_question"]
    return scenario["context"]["budget_pressure"]


def _prior_history():
    """The scenario's own `.good` transcript, minus its final answer — the
    exact prior conversation the evaluated turn actually receives."""
    transcript = fixtures.load_transcript(fixtures.TRANSCRIPTS_DIR / "long_history_latest_question.good.json")
    messages = [{"role": m["role"], "content": m["content"]} for m in transcript["messages"]]
    assert messages[-1]["role"] == "assistant"
    return messages[:-1]


class TestBudgetPressureIsReal:
    def test_stated_capacity_assumption_is_explicit(self):
        pressure = _pressure_assumption()
        assert isinstance(pressure["capacity_tokens"], int) and pressure["capacity_tokens"] > 0
        assert pressure["capacity_source"], "the fixture must state WHERE its capacity assumption comes from"
        assert isinstance(pressure["reserve_tokens"], int)
        history = _prior_history()
        assert len(history) > 10, "needs enough turns to meaningfully exceed a compact budget"

    def test_history_exceeds_compact_budget_and_drops_older_turns(self):
        pressure = _pressure_assumption()
        history = _prior_history()
        outcome = context_budget.enforce_budget(
            capacity_tokens=pressure["capacity_tokens"],
            capacity_source="test",
            reserve_tokens=pressure["reserve_tokens"],
            system_message=None,
            messages=history,
            tool_schemas=None,
        )
        ledger = outcome.ledger
        assert ledger["messages_dropped"] > 0, (
            "the fixture must actually exceed the stated compact budget - if nothing was "
            "dropped this isn't pressure, it's just a long prose label"
        )
        assert ledger["messages_sent"] < ledger["messages_total"]

    def test_latest_question_survives_the_trim(self):
        pressure = _pressure_assumption()
        history = _prior_history()
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
        pressure = _pressure_assumption()
        history = _prior_history()
        outcome = context_budget.enforce_budget(
            capacity_tokens=1_000_000,
            capacity_source="test",
            reserve_tokens=pressure["reserve_tokens"],
            system_message=None,
            messages=history,
            tool_schemas=None,
        )
        assert outcome.ledger["messages_dropped"] == 0

    def test_every_user_turn_the_scenario_sends_is_part_of_this_same_history(self):
        """The scenario's own `user_turns` (what a live `run` actually sends
        as real turns) must be exactly the questions in this pressurized
        history - proving the pressure reaches the evaluated conversation
        instead of living only in a side fixture."""
        scenario = fixtures.load_all_scenarios()["long_history_latest_question"]
        history = _prior_history()
        history_questions = [m["content"] for m in history if m["role"] == "user"]
        assert scenario["user_turns"] == history_questions
