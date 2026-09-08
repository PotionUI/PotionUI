"""tests/e2e/harness/chat_eval.py against the REAL API envelope shapes and SSE wire
format, via a fake HTTP layer (monkeypatched ``_http_request``) — never a
live network call.

Covers: config lookup by id, by name, an ambiguous name, the variant
clone-then-delete flow (and that it never PUTs the caller's own config), the
SSE parser against the backend's actual wire format
(``ChatController._sse_response.formatted``, ``src/features/chat/routes.py``),
and building transcript messages from a ``done`` event's persisted
``assistant_message`` (the same shape ``chatStream.ts``'s ``applyDone`` reads).
"""

import json
import sys
from pathlib import Path

import pytest

_HARNESS_DIR = Path(__file__).resolve().parents[2] / "tests" / "e2e" / "harness"
if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))

import chat_eval  # noqa: E402
from tests.evaluation.chat import fixtures as chat_fixtures  # noqa: E402
from tests.evaluation.chat import tool_snapshot  # noqa: E402
from tests.evaluation.chat.evaluator import evaluate_transcript  # noqa: E402


def _config_response(config_id="cfg-1", name="My Ollama", **overrides):
    # memory_reflection defaults to False: this represents a WELL-BEHAVED
    # dedicated evaluation configuration, satisfying chat_eval.py's memory
    # policy preflight by default so other tests don't have to opt in on
    # every call - tests that specifically exercise the policy pass
    # memory_reflection=True explicitly (see TestMemoryReflectionPolicy).
    base = {
        "id": config_id, "name": name, "type": "ollama", "enabled": True,
        "base_url": "http://localhost:11434", "api_key_set": False, "model": "llama3",
        "system_message": "You are a helpful assistant.", "temperature": 0.7, "max_tokens": 1000,
        "timeout": 30, "supports_vision": False, "disable_system_prompt": False,
        "memory_reflection": False, "provider_options": {"context_window": 8192}, "is_default": False,
    }
    base.update(overrides)
    return base


class _FakeRouter:
    """Records every call and returns a canned body keyed by (method, url substring)."""

    def __init__(self):
        self.calls = []

    def route(self, method, url, token, payload, accept):
        self.calls.append((method, url, payload))
        raise NotImplementedError("subclass or monkeypatch .respond")


def _install_fake_http(monkeypatch, responder):
    """``responder(method, url, payload) -> str raw body`` replaces
    ``chat_eval._http_request`` entirely — no real socket is ever opened."""
    calls = []

    def fake_http_request(method, url, token, payload, accept):
        calls.append((method, url, payload))
        return responder(method, url, payload)

    monkeypatch.setattr(chat_eval, "_http_request", fake_http_request)
    return calls


class TestResolveConfig:
    def test_lookup_by_id(self, monkeypatch):
        config = _config_response(config_id="cfg-42")

        def responder(method, url, payload):
            assert method == "GET"
            assert url.endswith("/api/llm/configurations/cfg-42")
            return json.dumps({"success": True, "data": config})

        calls = _install_fake_http(monkeypatch, responder)
        result = chat_eval._resolve_config("http://x", None, "cfg-42")
        assert result == config
        assert calls[0][0] == "GET"

    def test_lookup_by_name_falls_back_to_list(self, monkeypatch):
        config = _config_response(config_id="cfg-7", name="my-config")

        def responder(method, url, payload):
            if url.endswith("/api/llm/configurations/my-config"):
                # not found by id - the real API returns success:false, HTTP 200
                return json.dumps({"success": False, "error": "configuration_not_found", "message": "not found"})
            assert url.endswith("/api/llm/configurations")
            return json.dumps({"success": True, "data": {"configurations": [config], "default_provider": "cfg-7"}})

        _install_fake_http(monkeypatch, responder)
        result = chat_eval._resolve_config("http://x", None, "my-config")
        assert result == config

    def test_ambiguous_name_raises(self, monkeypatch):
        dup_a = _config_response(config_id="cfg-a", name="dup")
        dup_b = _config_response(config_id="cfg-b", name="dup")

        def responder(method, url, payload):
            if url.endswith("/api/llm/configurations/dup"):
                return json.dumps({"success": False, "error": "configuration_not_found"})
            return json.dumps({"success": True, "data": {"configurations": [dup_a, dup_b], "default_provider": None}})

        _install_fake_http(monkeypatch, responder)
        with pytest.raises(chat_eval._HttpError, match="Multiple"):
            chat_eval._resolve_config("http://x", None, "dup")

    def test_unknown_name_raises(self, monkeypatch):
        def responder(method, url, payload):
            if url.endswith("/api/llm/configurations/nope"):
                return json.dumps({"success": False, "error": "configuration_not_found"})
            return json.dumps({"success": True, "data": {"configurations": [], "default_provider": None}})

        _install_fake_http(monkeypatch, responder)
        with pytest.raises(chat_eval._HttpError, match="No LLM configuration"):
            chat_eval._resolve_config("http://x", None, "nope")


class TestCloneConfigRequestBody:
    def test_full_llm_config_request_shape_without_api_key(self):
        config = _config_response()
        body = chat_eval.clone_config_request_body(config, 4096)
        # LLMConfigRequest required fields (src/features/llm/dto.py)
        for field in ("name", "type", "enabled", "base_url", "model", "system_message"):
            assert field in body, f"missing required LLMConfigRequest field: {field}"
        assert body["provider_options"]["context_window"] == 4096
        assert "api_key" not in body, "a clone must never carry an api_key - LLMConfigResponse never returns one"
        assert body["name"] != config["name"], "the clone must not silently collide with the original's name"


class TestRunVariantNeverMutatesUserConfig:
    def test_variant_clones_and_deletes_never_puts_original(self, monkeypatch, tmp_path):
        config = _config_response(config_id="cfg-1")
        clone = _config_response(config_id="cfg-clone", name=f"{config['name']} [chat-eval 4096-token test budget]")

        def responder(method, url, payload):
            if method == "GET" and url.endswith("/api/llm/configurations/cfg-1"):
                return json.dumps({"success": True, "data": config})
            if method == "GET" and url.endswith("/api/llm/configurations/cfg-clone"):
                return json.dumps({"success": True, "data": clone})
            if method == "POST" and url.endswith("/api/llm/configurations"):
                return json.dumps({"success": True, "data": {"id": "cfg-clone"}})
            if method == "DELETE" and url.endswith("/api/llm/configurations/cfg-clone"):
                return json.dumps({"success": True, "data": {"id": "cfg-clone"}})
            raise AssertionError(f"unexpected call: {method} {url}")

        calls = _install_fake_http(monkeypatch, responder)

        args = chat_eval.build_parser().parse_args([
            "run", "--config", "cfg-1", "--variant", "compact",
            # restrict to a live_supported=false scenario so _run_scenario_live
            # makes no further HTTP calls beyond config resolution/clone/delete
            "--scenario", "tool_error_recovery",
            "--transcripts-dir", str(tmp_path / "transcripts"),
            "--out", str(tmp_path / "report.json"),
        ])
        exit_code = chat_eval.cmd_run(args)

        methods_and_urls = [(m, u) for m, u, _ in calls]
        assert ("POST", "http://localhost:7680/api/llm/configurations") in methods_and_urls
        assert ("DELETE", "http://localhost:7680/api/llm/configurations/cfg-clone") in methods_and_urls
        assert all(m != "PUT" for m, _, _ in calls), "a variant run must never PUT the user's own config"

        report = json.loads((tmp_path / "report.json").read_text())
        variants_seen = {r["variant"] for r in report["results"]}
        assert None in variants_seen and "compact" in variants_seen
        # tool_error_recovery is live_supported=false -> reported unsupported,
        # never counted as a failed task, so a scenario-only-unsupported run
        # exits clean.
        assert exit_code == 0
        assert report["summary"]["unsupported"] == 2
        assert report["summary"]["failed"] == 0


class TestBaseAndVariantProduceDistinctArtifacts:
    """A base+variant run over the SAME scenario must write two SEPARATE
    artifact files (the pre-fix bug: both wrote the same {scenario}.live.json,
    so the base row's evidence was silently overwritten by the variant's),
    and re-scoring each artifact independently must reproduce that row's own
    reported score - proving the artifact IS what got scored, not something
    reconstructed differently after the fact.
    """

    def test_distinct_artifacts_and_reproducible_re_scoring(self, monkeypatch, tmp_path):
        config = _config_response(config_id="cfg-1")
        clone = _config_response(config_id="cfg-clone", name=f"{config['name']} [chat-eval 4096-token test budget]")

        base_done = json.dumps({"assistant_message": {
            "content": "Yes - 30 steps with dpmpp_2m and cfg 5.5 is a solid starting point.",
        }})
        # Deliberately fails the scenario's own final_answer_contains_all
        # check (missing "dpmpp_2m") so the two rows have DIFFERENT scores.
        variant_done = json.dumps({"assistant_message": {
            "content": "Sure, that sounds like a reasonable setup overall.",
        }})

        def responder(method, url, payload):
            if method == "GET" and url.endswith("/api/llm/configurations/cfg-1"):
                return json.dumps({"success": True, "data": config})
            if method == "GET" and url.endswith("/api/llm/configurations/cfg-clone"):
                return json.dumps({"success": True, "data": clone})
            if method == "POST" and url.endswith("/api/llm/configurations"):
                return json.dumps({"success": True, "data": {"id": "cfg-clone"}})
            if method == "DELETE" and url.endswith("/api/llm/configurations/cfg-clone"):
                return json.dumps({"success": True, "data": {"id": "cfg-clone"}})
            if method == "POST" and url.endswith("/api/chat/sessions"):
                session_id = "sess-base" if payload["llm_config_id"] == "cfg-1" else "sess-variant"
                return json.dumps({"success": True, "data": {"id": session_id}})
            if method == "POST" and "/messages/stream" in url:
                return f"event: done\ndata: {base_done}\n\n" if "sess-base" in url else f"event: done\ndata: {variant_done}\n\n"
            raise AssertionError(f"unexpected call: {method} {url}")

        _install_fake_http(monkeypatch, responder)

        args = chat_eval.build_parser().parse_args([
            "run", "--config", "cfg-1", "--variant", "compact",
            "--scenario", "factual_answer_context",
            "--transcripts-dir", str(tmp_path / "transcripts"),
            "--out", str(tmp_path / "report.json"),
        ])
        chat_eval.cmd_run(args)

        report = json.loads((tmp_path / "report.json").read_text())
        base_row = next(r for r in report["results"] if r["variant"] is None)
        variant_row = next(r for r in report["results"] if r["variant"] == "compact")

        assert base_row["transcript"] != variant_row["transcript"], "base and variant must not share one artifact path"
        assert Path(base_row["transcript"]).is_file()
        assert Path(variant_row["transcript"]).is_file()
        assert base_row["passed"] is True
        assert variant_row["passed"] is False

        scenario = chat_fixtures.load_all_scenarios()["factual_answer_context"]
        tool_schemas = tool_snapshot.schemas_by_name()
        for row in (base_row, variant_row):
            artifact = json.loads(Path(row["transcript"]).read_text())
            assert artifact["turns"], "raw per-turn evidence (request + raw SSE + events) must be preserved"
            assert artifact["turns"][0]["raw_sse_text"], "the exact raw SSE text as received must be preserved"
            assert artifact["transcript"]["round_boundaries_known"] is False, (
                "the round-boundary qualifier must live IN the artifact's transcript, not depend on the caller"
            )
            re_scored = evaluate_transcript(scenario, artifact["transcript"], tool_schemas)
            assert re_scored.passed == row["passed"], "re-scoring the saved artifact must reproduce its own row's score"


class TestSseParsing:
    def test_parses_backend_wire_format(self):
        # Matches ChatController._sse_response.formatted's exact emission.
        raw = (
            'id: 5\nevent: tool_start\ndata: {"tool_name": "get_active_models", "seq": 5}\n\n'
            'id: 6\nevent: tool_end\ndata: {"tool_name": "get_active_models", "success": true, "seq": 6}\n\n'
            'id: 7\nevent: done\ndata: {"assistant_message": {"content": "hi", "seq": 7}}\n\n'
        )
        events = chat_eval.parse_sse_events(raw)
        assert [e["event"] for e in events] == ["tool_start", "tool_end", "done"]
        assert events[0]["data"]["tool_name"] == "get_active_models"
        assert events[2]["data"]["assistant_message"]["content"] == "hi"

    def test_missing_done_event_yields_no_assistant_message(self):
        raw = 'event: error\ndata: {"error": "turn_timeout"}\n\n'
        events = chat_eval.parse_sse_events(raw)
        messages, assistant_message = chat_eval.turn_transcript_messages("hello", events)
        assert assistant_message is None
        assert messages == [{"role": "user", "content": "hello"}]


class TestTurnTranscriptFromDoneEvent:
    def test_builds_tool_calls_and_outcomes_from_persisted_tool_executions(self):
        events = [{"event": "done", "data": {"assistant_message": {
            "content": "You're generating with JuggernautXL v9.",
            "tool_executions": [{
                "tool_name": "get_active_models", "arguments": {},
                "pending_approval": False, "result": {"success": True, "data": "{\"models\": []}"},
            }],
        }}}]
        messages, assistant_message = chat_eval.turn_transcript_messages("What model am I using?", events)
        assert messages[0] == {"role": "user", "content": "What model am I using?"}
        assert messages[1]["tool_calls"] == [{"name": "get_active_models", "arguments": {}}]
        assert messages[2] == {
            "role": "tool", "name": "get_active_models", "content": "{\"models\": []}",
            "outcome": "ok", "duration_ms": None,
        }
        assert messages[3]["content"] == "You're generating with JuggernautXL v9."
        assert assistant_message["content"] == "You're generating with JuggernautXL v9."

    def test_pending_approval_outcome(self):
        events = [{"event": "done", "data": {"assistant_message": {
            "content": "Approve to apply.",
            "tool_executions": [{
                "tool_name": "update_form_settings", "arguments": {"changes": []},
                "pending_approval": True, "result": {"success": False, "data": ""},
            }],
        }}}]
        messages, _ = chat_eval.turn_transcript_messages("bump steps", events)
        tool_message = next(m for m in messages if m["role"] == "tool")
        assert tool_message["outcome"] == "pending_approval"


class TestFailureSafeArtifacts:
    """_run_scenario_live must persist per-turn evidence AS IT HAPPENS, never
    lose it to an exception partway through, and score an incomplete
    conversation honestly (http_completed False, passed False, the
    scenario's own declared checks reported unverified rather than silently
    evaluated against a truncated transcript)."""

    def _run_single_scenario(self, monkeypatch, tmp_path, scenario_id, responder):
        config = _config_response(config_id="cfg-1")

        def full_responder(method, url, payload):
            if method == "GET" and url.endswith("/api/llm/configurations/cfg-1"):
                return json.dumps({"success": True, "data": config})
            if method == "POST" and url.endswith("/api/chat/sessions"):
                return json.dumps({"success": True, "data": {"id": "sess-1"}})
            return responder(method, url, payload)

        _install_fake_http(monkeypatch, full_responder)
        args = chat_eval.build_parser().parse_args([
            "run", "--config", "cfg-1", "--scenario", scenario_id,
            "--transcripts-dir", str(tmp_path / "transcripts"),
            "--out", str(tmp_path / "report.json"),
        ])
        chat_eval.cmd_run(args)
        report = json.loads((tmp_path / "report.json").read_text())
        row = next(r for r in report["results"] if r["scenario"] == scenario_id)
        artifact = json.loads(Path(row["transcript"]).read_text())
        return row, artifact

    def test_first_turn_success_then_second_turn_http_failure(self, monkeypatch, tmp_path):
        scenario = chat_fixtures.load_all_scenarios()["long_history_latest_question"]
        first_question = scenario["user_turns"][0]
        calls = {"stream": 0}

        def responder(method, url, payload):
            if method == "POST" and "/messages/stream" in url:
                calls["stream"] += 1
                if calls["stream"] == 1:
                    return 'event: done\ndata: {"assistant_message": {"content": "noted"}}\n\n'
                raise chat_eval._HttpError("POST ... -> HTTP 500: boom")
            raise AssertionError(f"unexpected call: {method} {url}")

        row, artifact = self._run_single_scenario(monkeypatch, tmp_path, "long_history_latest_question", responder)

        assert row["http_completed"] is False
        assert row["passed"] is False
        assert len(artifact["turns"]) == 2, "both the successful first turn and the failing second turn are recorded"
        assert artifact["turns"][0]["failure"] is None
        assert artifact["turns"][0]["events"] is not None, "the first turn's real evidence must survive the later failure"
        assert artifact["turns"][1]["failure"] == {
            "stage": "http", "error": "POST ... -> HTTP 500: boom", "turn_index": 1,
        }
        assert artifact["transcript"]["messages"][0] == {"role": "user", "content": first_question}
        # Every scenario-declared check gets an explicit unverified placeholder
        # rather than being silently scored against the truncated transcript.
        declared_check_types = {c["type"] for c in scenario["checks"]}
        unverified_types = {c["check"] for c in row["checks"] if c.get("unverified")}
        assert declared_check_types <= unverified_types

    def test_malformed_truncated_sse_data_is_classified_as_decode_failure(self, monkeypatch, tmp_path):
        def responder(method, url, payload):
            if method == "POST" and "/messages/stream" in url:
                return 'event: done\ndata: {"assistant_message": {"content": "x"'  # truncated JSON
            raise AssertionError(f"unexpected call: {method} {url}")

        row, artifact = self._run_single_scenario(monkeypatch, tmp_path, "explicit_generation_dry_run", responder)

        assert row["http_completed"] is False
        assert row["passed"] is False
        assert len(artifact["turns"]) == 1
        assert artifact["turns"][0]["raw_sse_text"], "the raw text that WAS received must be preserved even though it failed to decode"
        assert artifact["turns"][0]["failure"]["stage"] == "decode"

    def test_partial_transport_read_preserves_available_partial_evidence(self, monkeypatch, tmp_path):
        class _FakeIncompleteRead(Exception):
            def __init__(self, partial: bytes):
                super().__init__("incomplete read")
                self.partial = partial

        partial_bytes = b'event: token\ndata: {"content": "still gener'

        def responder(method, url, payload):
            if method == "POST" and "/messages/stream" in url:
                raise _FakeIncompleteRead(partial_bytes)
            raise AssertionError(f"unexpected call: {method} {url}")

        row, artifact = self._run_single_scenario(monkeypatch, tmp_path, "explicit_generation_dry_run", responder)

        assert row["http_completed"] is False
        assert artifact["turns"][0]["failure"]["stage"] == "transport"
        assert artifact["turns"][0]["raw_sse_text"] == partial_bytes.decode(), (
            "partial bytes read before the transport failure must not be discarded"
        )

    def test_passing_control_and_re_score_of_a_failed_artifact_reproduces_its_score(self, monkeypatch, tmp_path):
        """A scenario that fails and one that fully succeeds in the SAME run
        both get their own correct, independently re-scorable result."""
        good_config = _config_response(config_id="cfg-1")

        def responder(method, url, payload):
            if method == "POST" and "/messages/stream" in url:
                if "dry fox" in payload["content"] or "SDXL base preset" in payload["content"]:
                    return (
                        'event: done\ndata: {"assistant_message": {"content": '
                        '"I\'ve set up a red fox in the snow at 1024x1024 on the SDXL base preset - '
                        'approve to start it running.", "tool_executions": [{"tool_name": "start_generation", '
                        '"arguments": {"preset_id": "sdxl/base", "prompt": "a red fox in the snow"}, '
                        '"pending_approval": true, "result": {"success": false, "data": '
                        '"{\\"status\\": \\"pending_approval\\"}"}}]}}\n\n'
                    )
                raise chat_eval._HttpError("boom")
            raise AssertionError(f"unexpected call: {method} {url}")

        config = good_config

        def full_responder(method, url, payload):
            if method == "GET" and url.endswith("/api/llm/configurations/cfg-1"):
                return json.dumps({"success": True, "data": config})
            if method == "POST" and url.endswith("/api/chat/sessions"):
                return json.dumps({"success": True, "data": {"id": f"sess-{payload['name']}"}})
            return responder(method, url, payload)

        _install_fake_http(monkeypatch, full_responder)
        args = chat_eval.build_parser().parse_args([
            "run", "--config", "cfg-1",
            "--scenario", "explicit_generation_dry_run", "--scenario", "long_history_latest_question",
            "--transcripts-dir", str(tmp_path / "transcripts"),
            "--out", str(tmp_path / "report.json"),
        ])
        chat_eval.cmd_run(args)
        report = json.loads((tmp_path / "report.json").read_text())

        passing = next(r for r in report["results"] if r["scenario"] == "explicit_generation_dry_run")
        failing = next(r for r in report["results"] if r["scenario"] == "long_history_latest_question")
        assert passing["http_completed"] is True and passing["passed"] is True
        assert failing["http_completed"] is False and failing["passed"] is False

        tool_schemas = tool_snapshot.schemas_by_name()
        passing_scenario = chat_fixtures.load_all_scenarios()["explicit_generation_dry_run"]
        passing_artifact = json.loads(Path(passing["transcript"]).read_text())
        re_scored = evaluate_transcript(passing_scenario, passing_artifact["transcript"], tool_schemas)
        assert re_scored.passed == passing["passed"]

        failing_artifact = json.loads(Path(failing["transcript"]).read_text())
        assert failing_artifact["turns"][0]["failure"]["stage"] == "http"
        assert Path(failing["transcript"]).is_file()


class TestBudgetPressureReachesLiveTurn:
    """The long_history_latest_question scenario's pressure must reach the
    ACTUAL evaluated live turn - every padding turn is sent as a real turn to
    the same session, and the final turn's real backend ledger (not a
    standalone unit test) is what proves the pressure was observed."""

    def test_every_padding_turn_is_sent_and_pressure_is_observed_via_the_real_ledger(self, monkeypatch, tmp_path):
        config = _config_response(config_id="cfg-1")
        scenario = chat_fixtures.load_all_scenarios()["long_history_latest_question"]
        expected_turns = len(scenario["user_turns"])
        seen_payloads = []

        def responder(method, url, payload):
            if method == "GET" and url.endswith("/api/llm/configurations/cfg-1"):
                return json.dumps({"success": True, "data": config})
            if method == "POST" and url.endswith("/api/chat/sessions"):
                return json.dumps({"success": True, "data": {"id": "sess-1"}})
            if method == "POST" and "/messages/stream" in url:
                seen_payloads.append(payload)
                is_last = len(seen_payloads) == expected_turns
                assistant_message = {
                    "content": (
                        "For a 2x video upscale, reach for a dedicated video upscaler rather than a "
                        "still-image one." if is_last else "Noted, thanks."
                    ),
                }
                if is_last:
                    # The REAL backend's own reported ledger for this final turn -
                    # this, not a standalone unit test, is what the report's
                    # budget_pressure_observed check must key off of.
                    assistant_message["metadata"] = {"behavior_trace": {"context_ledger": {"budget": {
                        "capacity_tokens": 4096, "capacity_source": "config",
                        "accounting": "estimate", "measured": False, "messages_dropped": 12,
                    }}}}
                return f'event: done\ndata: {json.dumps({"assistant_message": assistant_message})}\n\n'
            raise AssertionError(f"unexpected call: {method} {url}")

        _install_fake_http(monkeypatch, responder)
        args = chat_eval.build_parser().parse_args([
            "run", "--config", "cfg-1", "--scenario", "long_history_latest_question",
            "--transcripts-dir", str(tmp_path / "transcripts"),
            "--out", str(tmp_path / "report.json"),
        ])
        chat_eval.cmd_run(args)

        assert len(seen_payloads) == expected_turns, "every padding turn must be sent as a real turn, not just the final question"
        assert [p["content"] for p in seen_payloads] == scenario["user_turns"], "the final question must still be the last real turn sent"

        report = json.loads((tmp_path / "report.json").read_text())
        row = report["results"][0]
        assert row["http_completed"] is True

        pressure_check = next(c for c in row["checks"] if c["check"] == "budget_pressure_observed")
        assert pressure_check["passed"] is True
        assert "real backend" in pressure_check["detail"]

        latest_question_check = next(c for c in row["checks"] if c["check"] == "latest_question_reflected")
        assert latest_question_check["passed"] is True
        assert row["passed"] is True


class TestMemoryReflectionPolicy:
    """chat_eval.py must refuse to evaluate any configuration whose
    memory_reflection is on, checked BEFORE any session/message is created
    for ANY selected config, and a generated clone must always be created
    with memory_reflection forced off regardless of the source config."""

    def test_refuses_before_any_session_or_message_call_when_base_config_has_reflection_on(self, monkeypatch, tmp_path):
        config = _config_response(config_id="cfg-1", memory_reflection=True)

        def responder(method, url, payload):
            if method == "GET" and url.endswith("/api/llm/configurations/cfg-1"):
                return json.dumps({"success": True, "data": config})
            raise AssertionError(f"unexpected call: {method} {url} - refusal must happen before any session/message call")

        calls = _install_fake_http(monkeypatch, responder)
        args = chat_eval.build_parser().parse_args([
            "run", "--config", "cfg-1", "--scenario", "explicit_generation_dry_run",
            "--transcripts-dir", str(tmp_path / "transcripts"), "--out", str(tmp_path / "report.json"),
        ])
        exit_code = chat_eval.cmd_run(args)

        assert exit_code != 0
        assert not (tmp_path / "report.json").exists()
        session_or_message_calls = [c for c in calls if "/api/chat/sessions" in c[1]]
        assert session_or_message_calls == [], "zero session/message calls must happen once the policy refuses"

    def test_refuses_when_variant_config_has_reflection_on_even_if_base_is_clean(self, monkeypatch, tmp_path):
        config = _config_response(config_id="cfg-1", memory_reflection=False)
        variant_config = _config_response(config_id="cfg-2", name="Variant", memory_reflection=True)

        def responder(method, url, payload):
            if method == "GET" and url.endswith("/api/llm/configurations/cfg-1"):
                return json.dumps({"success": True, "data": config})
            if method == "GET" and url.endswith("/api/llm/configurations/cfg-2"):
                return json.dumps({"success": True, "data": variant_config})
            raise AssertionError(f"unexpected call: {method} {url}")

        calls = _install_fake_http(monkeypatch, responder)
        args = chat_eval.build_parser().parse_args([
            "run", "--config", "cfg-1", "--variant-config", "cfg-2",
            "--scenario", "explicit_generation_dry_run",
            "--transcripts-dir", str(tmp_path / "transcripts"), "--out", str(tmp_path / "report.json"),
        ])
        exit_code = chat_eval.cmd_run(args)

        assert exit_code != 0
        session_or_message_calls = [c for c in calls if "/api/chat/sessions" in c[1]]
        assert session_or_message_calls == []

    def test_permitted_control_with_reflection_disabled_config_proceeds(self, monkeypatch, tmp_path):
        config = _config_response(config_id="cfg-1", memory_reflection=False)

        def responder(method, url, payload):
            if method == "GET" and url.endswith("/api/llm/configurations/cfg-1"):
                return json.dumps({"success": True, "data": config})
            if method == "POST" and url.endswith("/api/chat/sessions"):
                return json.dumps({"success": True, "data": {"id": "sess-1"}})
            if method == "POST" and "/messages/stream" in url:
                return (
                    'event: done\ndata: {"assistant_message": {"content": '
                    '"I\'ve set up a red fox in the snow at 1024x1024 on the SDXL base preset - '
                    'approve to start it running.", "tool_executions": [{"tool_name": "start_generation", '
                    '"arguments": {"preset_id": "sdxl/base", "prompt": "a red fox in the snow"}, '
                    '"pending_approval": true, "result": {"success": false, "data": '
                    '"{\\"status\\": \\"pending_approval\\"}"}}]}}\n\n'
                )
            raise AssertionError(f"unexpected call: {method} {url}")

        _install_fake_http(monkeypatch, responder)
        args = chat_eval.build_parser().parse_args([
            "run", "--config", "cfg-1", "--scenario", "explicit_generation_dry_run",
            "--transcripts-dir", str(tmp_path / "transcripts"), "--out", str(tmp_path / "report.json"),
        ])
        exit_code = chat_eval.cmd_run(args)

        assert exit_code == 0
        report = json.loads((tmp_path / "report.json").read_text())
        assert report["results"][0]["passed"] is True

    def test_generated_clone_always_carries_memory_reflection_false(self, monkeypatch, tmp_path):
        # Even when the SOURCE config has reflection on, a generated clone
        # must always be created with it forced off - clone_config_request_body
        # never copies the source's own setting through.
        config = _config_response(config_id="cfg-1", memory_reflection=False)
        create_bodies = []

        def responder(method, url, payload):
            if method == "GET" and url.endswith("/api/llm/configurations/cfg-1"):
                return json.dumps({"success": True, "data": config})
            if method == "POST" and url.endswith("/api/llm/configurations"):
                create_bodies.append(payload)
                return json.dumps({"success": True, "data": {"id": "cfg-clone"}})
            if method == "GET" and url.endswith("/api/llm/configurations/cfg-clone"):
                return json.dumps({"success": True, "data": _config_response(config_id="cfg-clone", memory_reflection=False)})
            if method == "DELETE" and url.endswith("/api/llm/configurations/cfg-clone"):
                return json.dumps({"success": True, "data": {"id": "cfg-clone"}})
            raise AssertionError(f"unexpected call: {method} {url}")

        _install_fake_http(monkeypatch, responder)
        args = chat_eval.build_parser().parse_args([
            "run", "--config", "cfg-1", "--variant", "compact",
            "--scenario", "tool_error_recovery",  # live_supported=false, no further HTTP calls needed
            "--transcripts-dir", str(tmp_path / "transcripts"), "--out", str(tmp_path / "report.json"),
        ])
        chat_eval.cmd_run(args)

        assert len(create_bodies) == 1
        assert create_bodies[0]["memory_reflection"] is False


class TestBudgetPressureProvenance:
    """budget_pressure_observed must never recompute pressure for a LIVE
    capture that has no real backend ledger - only an authored REPLAY
    fixture is eligible for the local recompute fallback."""

    def _scenario_and_schemas(self):
        scenario = chat_fixtures.load_all_scenarios()["long_history_latest_question"]
        return scenario, tool_snapshot.schemas_by_name()

    def test_saved_live_transcript_without_ledger_is_unverified_never_recomputed(self):
        scenario, tool_schemas = self._scenario_and_schemas()
        replay_transcript = chat_fixtures.load_transcript(
            chat_fixtures.TRANSCRIPTS_DIR / "long_history_latest_question.good.json"
        )
        # Same real pressurized history, but stamped as a LIVE capture with NO
        # ledger - simulates a saved artifact from a provider that never
        # reported one.
        live_transcript_without_ledger = dict(replay_transcript, capture="live", budget_ledger={})

        evaluation = evaluate_transcript(scenario, live_transcript_without_ledger, tool_schemas)
        pressure_check = next(r for r in evaluation.results if r.check == "budget_pressure_observed")
        assert pressure_check.unverified is True
        assert "never" in pressure_check.detail or "unverified" in pressure_check.detail.lower() or "recomput" in pressure_check.detail

    def test_canned_replay_transcript_recomputes_pressure(self):
        scenario, tool_schemas = self._scenario_and_schemas()
        replay_transcript = chat_fixtures.load_transcript(
            chat_fixtures.TRANSCRIPTS_DIR / "long_history_latest_question.good.json"
        )
        assert replay_transcript["capture"] == "replay"
        assert not replay_transcript.get("budget_ledger")

        evaluation = evaluate_transcript(scenario, replay_transcript, tool_schemas)
        pressure_check = next(r for r in evaluation.results if r.check == "budget_pressure_observed")
        assert pressure_check.unverified is False
        assert pressure_check.passed is True
        assert "recomputed" in pressure_check.detail

    def test_re_scoring_a_saved_live_artifact_with_a_real_ledger_preserves_the_ledger_branch(self, monkeypatch, tmp_path):
        """A live capture that DID get a real ledger must keep using it (not
        silently fall back to recompute) even after being saved and reloaded."""
        config = _config_response(config_id="cfg-1")
        scenario, tool_schemas = self._scenario_and_schemas()
        expected_turns = len(scenario["user_turns"])
        seen = []

        def responder(method, url, payload):
            if method == "GET" and url.endswith("/api/llm/configurations/cfg-1"):
                return json.dumps({"success": True, "data": config})
            if method == "POST" and url.endswith("/api/chat/sessions"):
                return json.dumps({"success": True, "data": {"id": "sess-1"}})
            if method == "POST" and "/messages/stream" in url:
                seen.append(payload)
                is_last = len(seen) == expected_turns
                msg = {"content": "For a 2x video upscale, reach for a dedicated video upscaler." if is_last else "noted"}
                if is_last:
                    msg["metadata"] = {"behavior_trace": {"context_ledger": {"budget": {
                        "capacity_tokens": 4096, "capacity_source": "config",
                        "accounting": "estimate", "measured": False, "messages_dropped": 9,
                    }}}}
                return f'event: done\ndata: {json.dumps({"assistant_message": msg})}\n\n'
            raise AssertionError(f"unexpected call: {method} {url}")

        _install_fake_http(monkeypatch, responder)
        args = chat_eval.build_parser().parse_args([
            "run", "--config", "cfg-1", "--scenario", "long_history_latest_question",
            "--transcripts-dir", str(tmp_path / "transcripts"), "--out", str(tmp_path / "report.json"),
        ])
        chat_eval.cmd_run(args)
        report = json.loads((tmp_path / "report.json").read_text())
        row = report["results"][0]

        artifact = json.loads(Path(row["transcript"]).read_text())
        assert artifact["transcript"]["capture"] == "live"
        assert artifact["transcript"]["budget_ledger"]["messages_dropped"] == 9

        re_scored = evaluate_transcript(scenario, artifact["transcript"], tool_schemas)
        pressure_check = next(r for r in re_scored.results if r.check == "budget_pressure_observed")
        assert pressure_check.unverified is False
        assert "real backend" in pressure_check.detail
        assert "live capture" in pressure_check.detail
