"""Evaluator fixtures: a good transcript passes, seven broken ones each fail
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

    def test_live_supported_scenarios_declare_context_metadata(self, scenarios):
        for scenario_id, scenario in scenarios.items():
            if scenario["live_supported"]:
                assert "live_context_metadata" in scenario, f"'{scenario_id}' is live_supported but has no live_context_metadata"
            else:
                assert scenario.get("live_unsupported_reason"), f"'{scenario_id}' is not live_supported but has no live_unsupported_reason"


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

    def test_unavailable_tool_fails_tool_validity(self, scenarios, tool_schemas):
        """get_active_models is a real, correctly-called tool - just not one
        'read_form_state' declared available, so it must still fail here."""
        result, _ = _evaluate("bad_unavailable_tool", scenarios, tool_schemas)
        assert not result.passed
        validity = next(r for r in result.results if r.check == "tool_calls_valid")
        assert not validity.passed, "expected tool validity to fail on a real tool outside the scenario's own tool set"
        assert "get_active_models" in validity.detail

    def test_success_before_error_fails_recovery_check(self, scenarios, tool_schemas):
        """A success recorded BEFORE the last error is not recovery from that error."""
        result, _ = _evaluate("bad_success_before_error", scenarios, tool_schemas)
        assert not result.passed
        recovery = next(r for r in result.results if r.check.startswith("error_then_recovery"))
        assert not recovery.passed, "expected recovery check to fail when the only success predates the last error"

    def test_missing_dry_run_evidence_fails_dry_run_check(self, scenarios, tool_schemas):
        """A missing/unknown outcome is not positive evidence of a dry run."""
        result, _ = _evaluate("bad_missing_dry_run_evidence", scenarios, tool_schemas)
        assert not result.passed
        dry_run = next(r for r in result.results if r.check.startswith("dry_run_never_enqueues"))
        assert not dry_run.passed, "expected dry-run check to fail when the tool outcome carries no dry-run evidence at all"

    def test_numeric_bound_violation_fails_tool_validity(self, scenarios, tool_schemas):
        """organize_gallery.rating has a real schema maximum of 5; 7 must fail."""
        result, _ = _evaluate("bad_numeric_bound_violation", scenarios, tool_schemas)
        assert not result.passed
        validity = next(r for r in result.results if r.check == "tool_calls_valid")
        assert not validity.passed, "expected tool validity to fail a rating above the schema's maximum"
        assert "maximum" in validity.detail
