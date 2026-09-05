"""Tests for the chat speed pass (Stage 5): prompt/tool memoization (B2),
token-budgeted history windowing (B3), and parallel @resource resolution (B4).

B1 (LLM config caching) and B5 (streamed tools-path answer) are covered in
tests/features/llm/test_manager.py-adjacent repository tests and
tests/features/llm/tools/test_tools_infrastructure.py::TestToolExecutorNativeStream
respectively.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.features.chat.context_builder import ChatContextBuilder
from src.features.chat.conversation import ConversationRunner, _DEFAULT_HISTORY_TOKEN_BUDGET
from src.features.chat.modes import ChatModeRegistry, build_generation_mode
from src.features.llm import context_budget


def _mode_registry() -> ChatModeRegistry:
    registry = ChatModeRegistry()
    registry.register(build_generation_mode())
    return registry


def _session(mode="generation", metadata=None):
    session = Mock()
    session.mode = mode
    session.metadata = metadata
    return session


# ---------------------------------------------------------------------------
# B2 — resolve_session_prompt_and_tools memoization
# ---------------------------------------------------------------------------

class TestResolveSessionPromptAndToolsCache:
    def _make_builder(self):
        manager = Mock()
        manager.chat_mode_registry = _mode_registry()
        registry = Mock()
        registry.get_for_mode.return_value = []
        registry.get_tool_hints_text.return_value = ""
        tool_executor = Mock()
        tool_executor.tool_registry = registry
        manager.tool_executor = tool_executor
        return ChatContextBuilder(manager), registry

    def test_second_call_is_a_cache_hit(self):
        """Same session (same mode/tools/system message/availability) — the second
        call is a cache hit: no new cache entry is written for it."""
        builder, registry = self._make_builder()
        session = _session()

        with patch.object(
            builder._prompt_tools_cache, "set", wraps=builder._prompt_tools_cache.set,
        ) as set_spy:
            prompt1, tools1, mode1 = builder.resolve_session_prompt_and_tools(session)
            assert set_spy.call_count == 1

            prompt2, tools2, mode2 = builder.resolve_session_prompt_and_tools(session)
            # Not recomputed: the cache hit never calls .set() again.
            assert set_spy.call_count == 1

        assert prompt1 == prompt2
        assert tools1 == tools2
        assert mode1.id == mode2.id

    def test_different_enabled_tools_signature_is_a_separate_cache_entry(self):
        builder, registry = self._make_builder()
        session_a = _session(metadata={"enabled_tools": ["echo"]})
        session_b = _session(metadata={"enabled_tools": ["other"]})

        builder.resolve_session_prompt_and_tools(session_a)
        builder.resolve_session_prompt_and_tools(session_b)

        assert registry.get_for_mode.call_count == 2

    def test_custom_system_message_bypasses_recompute_on_repeat(self):
        builder, registry = self._make_builder()
        session = _session(metadata={"system_message": "Be terse."})

        with patch.object(
            builder._prompt_tools_cache, "set", wraps=builder._prompt_tools_cache.set,
        ) as set_spy:
            prompt1, _, _ = builder.resolve_session_prompt_and_tools(session)
            prompt2, _, _ = builder.resolve_session_prompt_and_tools(session)
            assert set_spy.call_count == 1

        assert prompt1 == "Be terse." == prompt2

    def test_cache_entry_expires_after_ttl(self):
        builder, registry = self._make_builder()
        session = _session()

        builder.resolve_session_prompt_and_tools(session)
        assert registry.get_for_mode.call_count == 1

        # Force the cached entry to look expired without sleeping in the test.
        with patch(
            "src.features.llm.ttl_cache.time.monotonic",
            return_value=time.monotonic() + 3600,
        ):
            builder.resolve_session_prompt_and_tools(session)

        assert registry.get_for_mode.call_count == 2


class TestResolvedPromptIsSessionAccurate:
    """The cache keys on the enabled-tools signature, so differing enabled sets
    resolve to differing (session-accurate) prompts — no stale cross-session bleed."""

    def _make_builder(self):
        from src.features.llm.tools.registry import ToolRegistry
        from src.features.llm.tools.builtin.form_context_tool import GetFormStateTool
        from src.features.llm.tools.builtin.active_models_tool import GetActiveModelsTool

        tool_registry = ToolRegistry()
        tool_registry.register(GetFormStateTool())
        tool_registry.register(GetActiveModelsTool())

        manager = Mock()
        manager.chat_mode_registry = _mode_registry()
        tool_executor = Mock()
        tool_executor.tool_registry = tool_registry
        manager.tool_executor = tool_executor
        return ChatContextBuilder(manager)

    def test_full_set_names_every_tool(self):
        builder = self._make_builder()
        prompt, allowed, _ = builder.resolve_session_prompt_and_tools(_session())
        assert set(allowed) == {"get_form_state", "get_active_models"}
        assert "get_form_state" in prompt
        assert "get_active_models" in prompt

    def test_disabling_a_tool_drops_it_from_the_prompt(self):
        builder = self._make_builder()
        session = _session(metadata={"enabled_tools": ["get_active_models"]})
        prompt, allowed, _ = builder.resolve_session_prompt_and_tools(session)
        assert allowed == ["get_active_models"]
        assert "get_form_state" not in prompt
        assert "get_active_models" in prompt

    def test_two_sessions_do_not_share_a_prompt(self):
        builder = self._make_builder()
        full, _, _ = builder.resolve_session_prompt_and_tools(_session())
        reduced, _, _ = builder.resolve_session_prompt_and_tools(
            _session(metadata={"enabled_tools": ["get_active_models"]})
        )
        assert full != reduced
        assert "get_form_state" in full
        assert "get_form_state" not in reduced


class TestFormStateAwareToolAvailability:
    """A tool's is_available(form_state) predicate is applied
    before allowed_names/schemas/hints are built, and the memo cache carries
    an availability dimension so an active turn never bleeds into an inactive
    one (or vice versa)."""

    def _make_builder(self):
        from src.features.llm.tools.registry import ToolRegistry
        from src.features.llm.tools.builtin.video_director_tool import GetVideoDirectorTool

        tool_registry = ToolRegistry()
        tool_registry.register(GetVideoDirectorTool())

        manager = Mock()
        manager.chat_mode_registry = _mode_registry()
        tool_executor = Mock()
        tool_executor.tool_registry = tool_registry
        manager.tool_executor = tool_executor
        return ChatContextBuilder(manager)

    def _active_form_state(self):
        return {"video_director": {"active": True, "doc": {}, "capabilities": {}}}

    def test_director_tools_excluded_when_form_state_absent(self):
        builder = self._make_builder()
        _prompt, allowed, _ = builder.resolve_session_prompt_and_tools(_session())
        assert allowed == []

    def test_director_tools_excluded_when_inactive(self):
        builder = self._make_builder()
        form_state = {"video_director": {"active": False}}
        _prompt, allowed, _ = builder.resolve_session_prompt_and_tools(
            _session(), form_state=form_state,
        )
        assert allowed == []

    def test_director_tools_included_when_active(self):
        builder = self._make_builder()
        _prompt, allowed, _ = builder.resolve_session_prompt_and_tools(
            _session(), form_state=self._active_form_state(),
        )
        assert set(allowed) == {"get_video_director"}

    def test_unavailable_tools_drop_from_hints_and_schemas(self):
        builder = self._make_builder()
        registry = builder._m.tool_executor.tool_registry

        _prompt, allowed_inactive, _ = builder.resolve_session_prompt_and_tools(_session())
        assert "get_video_director" not in registry.get_tool_hints_text(allowed_inactive)
        assert registry.get_schemas(allowed_inactive) == []

        _prompt, allowed_active, _ = builder.resolve_session_prompt_and_tools(
            _session(), form_state=self._active_form_state(),
        )
        assert "get_video_director" in registry.get_tool_hints_text(allowed_active)
        schema_names = {s["function"]["name"] for s in registry.get_schemas(allowed_active)}
        assert schema_names == {"get_video_director"}

    def test_cache_does_not_leak_active_prompt_into_inactive_turn(self):
        builder = self._make_builder()
        session = _session()

        active_prompt, active_allowed, _ = builder.resolve_session_prompt_and_tools(
            session, form_state=self._active_form_state(),
        )
        inactive_prompt, inactive_allowed, _ = builder.resolve_session_prompt_and_tools(
            session, form_state=None,
        )

        assert active_allowed != inactive_allowed
        assert inactive_allowed == []
        assert "get_video_director" in active_prompt
        assert "get_video_director" not in inactive_prompt

    def test_cache_does_not_leak_inactive_prompt_into_active_turn(self):
        """Same scenario, opposite call order — the cache key must key on
        availability regardless of which turn happened to run first."""
        builder = self._make_builder()
        session = _session()

        inactive_prompt, inactive_allowed, _ = builder.resolve_session_prompt_and_tools(
            session, form_state=None,
        )
        active_prompt, active_allowed, _ = builder.resolve_session_prompt_and_tools(
            session, form_state=self._active_form_state(),
        )

        assert inactive_allowed == []
        assert set(active_allowed) == {"get_video_director"}
        assert active_prompt != inactive_prompt


# ---------------------------------------------------------------------------
# B3 — token-budgeted history window
# ---------------------------------------------------------------------------

class TestApplyHistoryBudget:
    def _history(self, n, content_len=100):
        return [
            {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * content_len}
            for i in range(n)
        ]

    def _runner(self, get_setting_return=None, get_setting_side_effect=None):
        manager = Mock()
        manager.settings.get_setting = Mock(
            return_value=get_setting_return, side_effect=get_setting_side_effect
        )
        return ConversationRunner(manager)

    def test_unlimited_when_budget_is_zero(self):
        runner = self._runner(get_setting_return=0)
        history = self._history(50)
        info = runner._apply_history_budget(history)
        assert info == {"messages_sent": 50, "messages_total": 50, "truncated": False}
        assert len(history) == 50

    def test_default_budget_used_when_setting_missing(self):
        # Settings.get_setting returns the caller-supplied default
        # when the key is unset - here, _DEFAULT_HISTORY_TOKEN_BUDGET itself.
        runner = self._runner(get_setting_return=_DEFAULT_HISTORY_TOKEN_BUDGET)
        # 8000 token default * 4 chars/token = 32000 chars; 50 * 100 = 5000 chars fits easily.
        history = self._history(50)
        info = runner._apply_history_budget(history)
        assert info["truncated"] is False
        assert len(history) == 50

    def test_drops_oldest_first_keeps_current_message(self):
        # Budget of 10 tokens = 40 chars. Each message is 100 chars, well over
        # budget on its own, but the current (last) message must never be dropped.
        runner = self._runner(get_setting_return=10)
        history = self._history(5)
        original_last = history[-1]
        info = runner._apply_history_budget(history)

        assert info["messages_total"] == 5
        assert info["truncated"] is True
        assert info["messages_sent"] == 1
        assert history == [original_last]

    def test_whole_messages_only_keeps_as_many_recent_as_fit(self):
        # Budget of 100 tokens = 400 chars. Messages are 100 chars each, so 4 fit.
        runner = self._runner(get_setting_return=100)
        history = self._history(6)
        expected_kept = history[-4:]
        info = runner._apply_history_budget(history)

        assert info["messages_total"] == 6
        assert info["messages_sent"] == 4
        assert info["truncated"] is True
        assert history == expected_kept

    def test_empty_history_is_a_noop(self):
        runner = self._runner()
        history = []
        info = runner._apply_history_budget(history)
        assert info == {"messages_sent": 0, "messages_total": 0, "truncated": False}

    def test_setting_read_failure_falls_back_to_default(self):
        runner = self._runner(get_setting_side_effect=RuntimeError("db down"))
        history = self._history(3)
        info = runner._apply_history_budget(history)
        assert info["truncated"] is False

    def test_min_protected_keeps_protected_tail_even_if_it_alone_busts_budget(self):
        # Budget of 10 tokens = 40 chars. The last 2 messages are 200 chars on
        # their own -- over budget by themselves -- but min_protected=2 must
        # keep both unconditionally rather than relying on the backward walk
        # happening to keep them.
        runner = self._runner(get_setting_return=10)
        history = self._history(5, content_len=100)
        protected_tail = history[-2:]

        info = runner._apply_history_budget(history, min_protected=2)

        assert info["messages_total"] == 5
        assert info["messages_sent"] == 2
        assert info["truncated"] is True
        assert history == protected_tail

    def test_min_protected_two_still_drops_everything_before_it_when_it_fits(self):
        # Budget of 100 tokens = 400 chars; messages are 50 chars each, so the
        # protected pair (100 chars) leaves room for 6 more.
        runner = self._runner(get_setting_return=100)
        history = self._history(10, content_len=50)
        expected_kept = history[-8:]

        info = runner._apply_history_budget(history, min_protected=2)

        assert info["messages_sent"] == 8
        assert history == expected_kept


# ---------------------------------------------------------------------------
# B6 — per-turn context ledger
# ---------------------------------------------------------------------------

class TestBuildContextLedger:
    def test_shape_and_totals(self):
        system_prompt = "s" * 40
        tool_schemas = [{"function": {"name": "get_data", "description": "d"}}]
        memory_result = {"injected_chars": 48}
        history = [
            {"role": "user", "content": "a" * 10},
            {"role": "assistant", "content": "b" * 20},
        ]

        ledger = ConversationRunner._build_context_ledger(system_prompt, tool_schemas, memory_result, history)

        assert ledger["system_prompt"] == {"chars": 40, "est_tokens": 10}
        assert ledger["memory"] == {"chars": 48, "est_tokens": 12}
        assert ledger["history"]["chars"] == 30
        assert ledger["history"]["message_count"] == 2
        assert ledger["tool_schemas"]["tool_count"] == 1
        assert ledger["tool_schemas"]["chars"] == len(json.dumps(tool_schemas))
        expected_total = ledger["system_prompt"]["chars"] + ledger["tool_schemas"]["chars"] + ledger["history"]["chars"]
        assert ledger["total_est_tokens"] == expected_total // 4

    def test_empty_inputs_yield_zeroed_ledger(self):
        ledger = ConversationRunner._build_context_ledger(None, [], {}, [])
        assert ledger["system_prompt"] == {"chars": 0, "est_tokens": 0}
        assert ledger["memory"] == {"chars": 0, "est_tokens": 0}
        assert ledger["history"] == {"chars": 0, "est_tokens": 0, "message_count": 0}
        assert ledger["tool_schemas"]["tool_count"] == 0
        assert ledger["total_est_tokens"] == 0

    def test_non_serializable_schemas_degrade_instead_of_raising(self):
        ledger = ConversationRunner._build_context_ledger("sys", [object()], {}, [])
        assert ledger["tool_schemas"] == {"chars": 2, "est_tokens": 0, "tool_count": 0}

    def test_budget_section_defaults_to_unknown_capacity_without_a_config(self):
        """No `capacity` passed (the shape every pre-existing caller of this
        method still uses) degrades to the labelled unknown-capacity default
        rather than crashing or silently skipping the new accounting."""
        ledger = ConversationRunner._build_context_ledger(
            "sys", [], {}, [{"role": "user", "content": "hi"}],
        )
        assert ledger["budget"]["capacity_source"] == "unknown"
        assert ledger["budget"]["capacity_tokens"] == context_budget.UNKNOWN_CAPACITY_TOKENS
        assert ledger["budget"]["measured"] is False

    def test_budget_section_uses_the_given_accounting_inputs(self):
        accounting = context_budget.AccountingInputs(
            capacity=context_budget.CapacityInfo(500, "config"),
            reserve_tokens=100,
            counter=lambda t: len(t),
        )
        ledger = ConversationRunner._build_context_ledger(
            "sys", [], {}, [{"role": "user", "content": "hello"}],
            accounting=accounting,
        )
        budget = ledger["budget"]
        assert budget["capacity_tokens"] == 500
        assert budget["capacity_source"] == "config"
        assert budget["reserve_tokens"] == 100
        assert budget["accounting"] == "fragments+framing"

    def test_budget_section_reports_chat_template_when_a_messages_counter_is_given(self):
        accounting = context_budget.AccountingInputs(
            capacity=context_budget.CapacityInfo(500, "config"),
            reserve_tokens=100,
            messages_counter=lambda system_message, messages, tools: 12,
        )
        ledger = ConversationRunner._build_context_ledger(
            None, [], {}, [{"role": "user", "content": "hello"}],
            accounting=accounting,
        )
        budget = ledger["budget"]
        assert budget["accounting"] == "chat_template"
        assert budget["measured"] is True
        assert budget["estimated_tokens"] == 12

    def test_irreducibly_oversized_history_raises(self):
        accounting = context_budget.AccountingInputs(
            capacity=context_budget.CapacityInfo(10, "config"), reserve_tokens=5, counter=len,
        )
        with pytest.raises(context_budget.ContextBudgetExceededError):
            ConversationRunner._build_context_ledger(
                None, [], {}, [{"role": "user", "content": "x" * 500}],
                accounting=accounting,
            )

    def test_image_attached_is_reflected_in_the_budget_section(self):
        ledger = ConversationRunner._build_context_ledger(
            "sys", [], {}, [{"role": "user", "content": "hi"}], image_data="base64...",
        )
        assert ledger["budget"]["image_tokens"] == context_budget.DEFAULT_IMAGE_TOKEN_ESTIMATE


class TestResolveBudgetInputs:
    """`_resolve_budget_inputs` is a thin, defensive wrapper around
    `LLMGateway.accounting_inputs_for` — the SAME builder the gateway uses
    for every real send (see TestLedgerMatchesGatewayHook in
    tests/features/llm/test_gateway.py for the end-to-end proof they can't
    diverge); what belongs here is the wrapper's own contract: resolve the
    config, pass through mode options, and degrade to None on any failure.
    """

    def _session(self, llm_config_id="llm-1"):
        session = Mock()
        session.llm_config_id = llm_config_id
        return session

    def test_no_llm_config_id_degrades_to_none(self):
        runner = ConversationRunner(Mock())
        result = runner._resolve_budget_inputs(self._session(llm_config_id=None), mode=None)
        assert result is None

    def test_delegates_to_the_gateways_shared_builder_with_no_mode_options(self):
        manager = Mock()
        config = Mock(type="ollama", provider_options={"num_ctx": 4096}, max_tokens=2000)
        manager.llm_service.repository.get_configuration.return_value = config
        expected = context_budget.AccountingInputs(
            capacity=context_budget.CapacityInfo(4096, "config"), reserve_tokens=2000,
        )
        manager.llm_service.accounting_inputs_for.return_value = expected
        runner = ConversationRunner(manager)

        result = runner._resolve_budget_inputs(self._session(), mode=None)

        assert result is expected
        manager.llm_service.accounting_inputs_for.assert_called_once_with(config, None)

    def test_mode_llm_options_is_passed_through_as_options_override(self):
        from types import SimpleNamespace

        manager = Mock()
        config = Mock(type="openai", provider_options={}, max_tokens=2000)
        manager.llm_service.repository.get_configuration.return_value = config
        manager.llm_service.accounting_inputs_for.return_value = context_budget.AccountingInputs(
            capacity=context_budget.CapacityInfo(context_budget.UNKNOWN_CAPACITY_TOKENS, "unknown"),
            reserve_tokens=512,
        )
        runner = ConversationRunner(manager)
        mode = SimpleNamespace(llm_options={"max_tokens": 512})

        result = runner._resolve_budget_inputs(self._session(), mode=mode)

        assert result.reserve_tokens == 512
        manager.llm_service.accounting_inputs_for.assert_called_once_with(config, {"max_tokens": 512})

    def test_config_lookup_failure_degrades_to_none_never_raises(self):
        manager = Mock()
        manager.llm_service.repository.get_configuration.side_effect = RuntimeError("db down")
        runner = ConversationRunner(manager)

        result = runner._resolve_budget_inputs(self._session(), mode=None)

        assert result is None

    def test_a_config_missing_expected_attributes_degrades_to_none(self):
        """A test-double config (e.g. `SimpleNamespace(provider_options={})`,
        the idiom several chat test modules already use) that lacks
        `max_tokens`/`type` must never crash the send — only degrade."""
        from types import SimpleNamespace

        manager = Mock()
        manager.llm_service.repository.get_configuration.return_value = SimpleNamespace(provider_options={})
        manager.llm_service.accounting_inputs_for.return_value = context_budget.AccountingInputs(
            capacity=context_budget.CapacityInfo(context_budget.UNKNOWN_CAPACITY_TOKENS, "unknown"),
            reserve_tokens=Mock(),  # non-numeric — exactly what a Mock-shaped max_tokens produces
        )
        runner = ConversationRunner(manager)

        result = runner._resolve_budget_inputs(self._session(), mode=None)

        assert result is None

    def test_a_fully_generic_mock_llm_service_degrades_to_none(self):
        """The common existing chat-test idiom — `llm_service` left as a bare
        `Mock()`/`AsyncMock()` with nothing configured — must never crash a
        send just because `accounting_inputs_for` auto-mocked to a generic
        Mock whose `reserve_tokens` isn't a real number."""
        manager = Mock()
        manager.llm_service.repository.get_configuration.return_value = Mock()
        runner = ConversationRunner(manager)

        result = runner._resolve_budget_inputs(self._session(), mode=None)

        assert result is None


class TestResolveToolSchemasForLedger:
    def test_no_allowed_tools_returns_empty(self):
        runner = ConversationRunner(Mock())
        assert runner._resolve_tool_schemas_for_ledger(None) == []
        assert runner._resolve_tool_schemas_for_ledger([]) == []

    def test_returns_real_schemas_from_the_registry(self):
        manager = Mock()
        manager.tool_executor.tool_registry.get_schemas.return_value = [
            {"function": {"name": "get_data"}}
        ]
        runner = ConversationRunner(manager)

        result = runner._resolve_tool_schemas_for_ledger(["get_data"])

        assert result == [{"function": {"name": "get_data"}}]
        manager.tool_executor.tool_registry.get_schemas.assert_called_once_with(["get_data"])

    def test_mock_registry_without_get_schemas_configured_degrades_to_empty(self):
        """A test double that only stubs get_for_mode/get_tool_hints_text (the
        common shape in this test suite) auto-vivifies get_schemas() to a Mock
        object, not a list -- this must degrade to [] rather than poison the
        ledger with a non-serializable value."""
        manager = Mock()
        runner = ConversationRunner(manager)

        result = runner._resolve_tool_schemas_for_ledger(["get_data"])

        assert result == []

    def test_no_tool_executor_returns_empty(self):
        manager = Mock()
        manager.tool_executor = None
        runner = ConversationRunner(manager)

        assert runner._resolve_tool_schemas_for_ledger(["get_data"]) == []


# ---------------------------------------------------------------------------
# B4 — parallel @resource resolution
# ---------------------------------------------------------------------------

class TestResolveMessageResourcesParallel:
    def _make_builder(self, providers):
        manager = Mock()
        registry = Mock()

        async def resolve(uri, ctx):
            return await providers[uri](uri, ctx)

        registry.resolve = AsyncMock(side_effect=resolve)
        manager.resource_registry = registry
        manager.model_index_manager = None
        manager.phrasebook_category_repository = None
        manager.phrasebook_value_repository = None
        manager.phrasebook_search = None
        manager.preset_manager = None
        manager.generation_repository = None
        manager.generation_parameter_repository = None
        manager.generation_model_repository = None
        return ChatContextBuilder(manager), registry

    @pytest.mark.asyncio
    async def test_resolves_concurrently_not_sequentially(self):
        """Two 50ms resolves should take ~50ms total, not ~100ms, if run concurrently."""
        async def slow(uri, ctx):
            await asyncio.sleep(0.05)
            return Mock(uri=uri)

        builder, _ = self._make_builder({"a.one": slow, "a.two": slow})

        start = time.monotonic()
        results = await builder.resolve_message_resources(["a.one", "a.two"], "user-1", "generation")
        elapsed = time.monotonic() - start

        assert elapsed < 0.09  # well under the ~0.1s a sequential loop would take
        assert [r.uri for r in results] == ["a.one", "a.two"]

    @pytest.mark.asyncio
    async def test_preserves_input_order_regardless_of_completion_order(self):
        async def fast(uri, ctx):
            return Mock(uri=uri)

        async def slow(uri, ctx):
            await asyncio.sleep(0.02)
            return Mock(uri=uri)

        # "b.slow" is requested first but finishes last.
        builder, _ = self._make_builder({"b.slow": slow, "b.fast": fast})

        results = await builder.resolve_message_resources(["b.slow", "b.fast"], "user-1", "generation")

        assert [r.uri for r in results] == ["b.slow", "b.fast"]

    @pytest.mark.asyncio
    async def test_one_failing_resource_does_not_take_down_the_batch(self):
        """A provider's resolve() never raises in production (registry.resolve() catches
        internally) — this proves that contract holds under concurrent resolution too:
        one resource reporting an error via its own return value doesn't affect siblings."""
        async def ok(uri, ctx):
            return Mock(uri=uri, kind="thing")

        async def erroring(uri, ctx):
            return Mock(uri=uri, kind="error")

        builder, _ = self._make_builder({"c.ok": ok, "c.bad": erroring})

        results = await builder.resolve_message_resources(["c.bad", "c.ok"], "user-1", "generation")

        assert [r.kind for r in results] == ["error", "thing"]

    @pytest.mark.asyncio
    async def test_no_resources_returns_empty_without_calling_registry(self):
        builder, registry = self._make_builder({})
        results = await builder.resolve_message_resources(None, "user-1", "generation")
        assert results == []
        registry.resolve.assert_not_called()
