"""scripts/chat_eval.py against the REAL API envelope shapes and SSE wire
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

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import chat_eval  # noqa: E402


def _config_response(config_id="cfg-1", name="My Ollama", **overrides):
    base = {
        "id": config_id, "name": name, "type": "ollama", "enabled": True,
        "base_url": "http://localhost:11434", "api_key_set": False, "model": "llama3",
        "system_message": "You are a helpful assistant.", "temperature": 0.7, "max_tokens": 1000,
        "timeout": 30, "supports_vision": False, "disable_system_prompt": False,
        "memory_reflection": True, "provider_options": {"context_window": 8192}, "is_default": False,
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

        from tests.evaluation.chat import fixtures as chat_fixtures, tool_snapshot
        from tests.evaluation.chat.evaluator import evaluate_transcript

        scenario = chat_fixtures.load_all_scenarios()["factual_answer_context"]
        tool_schemas = tool_snapshot.schemas_by_name()
        for row in (base_row, variant_row):
            artifact = json.loads(Path(row["transcript"]).read_text())
            assert artifact["turns"], "raw per-turn evidence (request + raw SSE + events) must be preserved"
            assert artifact["turns"][0]["raw_sse_text"], "the exact raw SSE text as received must be preserved"
            re_scored = evaluate_transcript(scenario, artifact["transcript"], tool_schemas, round_boundaries_known=False)
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
