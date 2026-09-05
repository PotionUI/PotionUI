"""Evaluator fixtures: a good transcript passes, three broken ones each fail
exactly the check they were built to demonstrate, and every scenario file's
declared tools resolve against the real tool registry.
"""

import pytest

from tests.evaluation.chat import fixtures, tool_snapshot
from tests.evaluation.chat.evaluator import evaluate_transcript


@pytest.fixture(scope="module")
def tool_schemas():
    return tool_snapshot.schemas_by_name()


@pytest.fixture(scope="module")
def scenarios():
    return fixtures.load_all_scenarios()


def _evaluate(name, scenarios, tool_schemas):
    transcript = fixtures.load_transcript(fixtures.TRANSCRIPTS_DIR / f"{name}.json")
    scenario = fixtures.scenario_for_transcript(transcript)
    return evaluate_transcript(scenario, transcript, tool_schemas), scenario


class TestFixtureSchemas:
    """Every scenario file loads and its declared tools exist in the real registry."""

    def test_every_scenario_loads(self, scenarios):
        assert len(scenarios) >= 11

    def test_scenario_tool_names_match_registry(self, scenarios, tool_schemas):
        for scenario_id, scenario in scenarios.items():
            tool_names = scenario["tool_names"]
            if tool_names is None:
                continue
            unknown = [name for name in tool_names if name not in tool_schemas]
            assert not unknown, f"scenario '{scenario_id}' declares unknown tool(s): {unknown}"

    def test_every_good_transcript_references_a_real_scenario(self, scenarios):
        for path in fixtures.iter_transcript_paths():
            transcript = fixtures.load_transcript(path)
            assert transcript["scenario"] in scenarios, f"{path.name} references unknown scenario '{transcript['scenario']}'"


class TestGoodTranscriptsPass:
    """Every '<scenario>.good' transcript satisfies its own scenario's checks."""

    @pytest.mark.parametrize("scenario_id", sorted(fixtures.load_all_scenarios().keys()))
    def test_good_transcript_passes(self, scenario_id, scenarios, tool_schemas):
        result, _ = _evaluate(f"{scenario_id}.good", scenarios, tool_schemas)
        failures = [(r.check, r.detail) for r in result.failures()]
        assert result.passed, f"'{scenario_id}.good' failed checks: {failures}"


class TestNegativeFixturesFailTheirIntendedCheck:
    """Each broken fixture fails specifically the check it was built to break."""

    def test_invalid_tool_call_fails_tool_validity(self, scenarios, tool_schemas):
        result, _ = _evaluate("bad_invalid_tool_call", scenarios, tool_schemas)
        assert not result.passed
        validity = next(r for r in result.results if r.check == "tool_calls_valid")
        assert not validity.passed, "expected the tool-call validity check to fail on a wrong-typed argument"
        assert "changes" in validity.detail

    def test_lost_current_question_fails_latest_question_check(self, scenarios, tool_schemas):
        result, _ = _evaluate("bad_lost_current_question", scenarios, tool_schemas)
        assert not result.passed
        latest = next(r for r in result.results if r.check == "latest_question_reflected")
        assert not latest.passed, "expected the latest-question check to fail when the answer re-litigates an earlier topic"

    def test_falsely_claimed_apply_fails_truthfulness_check(self, scenarios, tool_schemas):
        result, _ = _evaluate("bad_falsely_claimed_apply", scenarios, tool_schemas)
        assert not result.passed
        truthfulness = next(r for r in result.results if r.check.startswith("truthful_apply_status"))
        assert not truthfulness.passed, "expected the truthfulness check to fail when a stale outcome is narrated as applied"
        assert "stale" in truthfulness.detail
