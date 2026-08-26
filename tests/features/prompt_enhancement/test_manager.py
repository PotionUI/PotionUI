"""Tests for PromptEnhancementManager."""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.features.prompt_enhancement.manager import PromptEnhancementManager


LONG_PROMPT = (
    "An oil painting of a graying red fox pausing on a moss-covered granite outcrop "
    "deep in an old-growth pine forest at the first light of dawn, low golden rays "
    "raking sideways through drifting ground fog, illuminating floating pollen and "
    "the fox's breath in the cold air, ferns and fallen birch trunks layering the "
    "foreground while distant conifers dissolve into pale blue haze, warm amber "
    "against cool teal shadows, visible brushwork, thick impasto highlights on the "
    "fur, a single magpie watching from a broken branch above, composition following "
    "a rising diagonal from the lower left ferns to the fox's alert upturned head."
)


class FakeLLMService:
    """Records generate_with_history calls and returns scripted responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def generate_with_history(self, messages, llm_id, custom_system_message=None,
                                    options_override=None, **kwargs):
        self.calls.append({
            "messages": messages,
            "llm_id": llm_id,
            "system": custom_system_message,
            "options": options_override or {},
        })
        content = self.responses.pop(0) if self.responses else LONG_PROMPT
        return SimpleNamespace(content=content)


def make_prompt(id_, text, source_url=None):
    return SimpleNamespace(id=id_, prompt=text, source_url=source_url)


def make_prompt_db(approved=None, community=None):
    db = MagicMock()
    calls = []

    async def search(**kwargs):
        calls.append(kwargs)
        if kwargs.get("source_provider") == "chat_approved":
            return list(approved or [])
        return list(community or [])

    db.search = search
    db.search_calls = calls
    return db


def make_manager(llm, prompt_db=None, feedback_repo=None):
    return PromptEnhancementManager(
        llm_service=llm,
        prompt_database=prompt_db,
        model_index_manager=None,
        feedback_repository=feedback_repo,
    )


async def run_enhance(manager, brief="a fox in a forest", n_candidates=2, **kwargs):
    events = []
    async for event in manager.enhance_stream(
        user_id="u-1", llm_id="llm-1", brief=brief, n_candidates=n_candidates, **kwargs
    ):
        events.append(event)
    return events


class TestPipelineStages:
    @pytest.mark.asyncio
    async def test_stage_ordering_and_events(self):
        llm = FakeLLMService(['["fox", "forest"]', "1. misty dawn\n2. neon night", LONG_PROMPT, LONG_PROMPT])
        manager = make_manager(llm)

        events = await run_enhance(manager)

        stages = [(e["type"], e.get("stage")) for e in events if e["type"] != "result"]
        assert stages == [
            ("stage_start", "enhance:gather"), ("stage_end", "enhance:gather"),
            ("stage_start", "enhance:ideate"), ("stage_end", "enhance:ideate"),
            ("stage_start", "enhance:write"), ("stage_end", "enhance:write"),
        ]
        result = events[-1]
        assert result["type"] == "result"
        assert len(result["data"]["candidates"]) == 2

    @pytest.mark.asyncio
    async def test_stage_sampling_overrides(self):
        llm = FakeLLMService(['["fox"]', "1. a\n2. b", LONG_PROMPT, LONG_PROMPT])
        manager = make_manager(llm)

        await run_enhance(manager)

        concept_call, ideate_call, write_call = llm.calls[0], llm.calls[1], llm.calls[2]
        assert concept_call["options"]["temperature"] == 0.3
        assert concept_call["options"]["think"] is False
        assert ideate_call["options"]["temperature"] == 1.1
        assert ideate_call["options"]["top_p"] == 0.95
        assert ideate_call["options"]["think"] is False
        assert write_call["options"]["temperature"] == 0.85

    @pytest.mark.asyncio
    async def test_malformed_concepts_falls_back_to_comma_split(self):
        llm = FakeLLMService(["not json at all", "1. a\n2. b", LONG_PROMPT, LONG_PROMPT])
        prompt_db = make_prompt_db()
        manager = make_manager(llm, prompt_db=prompt_db)

        await run_enhance(manager, brief="red fox, pine forest")

        community_queries = [c["query"] for c in prompt_db.search_calls if not c.get("source_provider")]
        assert "red fox" in community_queries
        assert "pine forest" in community_queries

    @pytest.mark.asyncio
    async def test_ideation_failure_still_writes(self):
        llm = FakeLLMService(['["fox"]'])
        original = llm.generate_with_history
        call_count = {"n": 0}

        async def flaky(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("ollama down")
            return await original(*args, **kwargs)

        llm.generate_with_history = flaky
        manager = make_manager(llm)

        events = await run_enhance(manager, n_candidates=2)

        result = events[-1]["data"]
        assert len(result["candidates"]) == 2
        assert all(c["text"] for c in result["candidates"])


class TestGrounding:
    @pytest.mark.asyncio
    async def test_approved_exemplars_come_before_community_in_write_prompt(self):
        approved = [make_prompt("a-1", "approved exemplar text")]
        community = [make_prompt("c-1", "community exemplar text")]
        llm = FakeLLMService(['["fox"]', "1. a", LONG_PROMPT])
        manager = make_manager(llm, prompt_db=make_prompt_db(approved, community))

        events = await run_enhance(manager, n_candidates=1)

        write_prompt = llm.calls[-1]["messages"][0]["content"]
        assert "[user-approved] approved exemplar text" in write_prompt
        assert "[community] community exemplar text" in write_prompt
        assert write_prompt.index("approved exemplar text") < write_prompt.index("community exemplar text")
        assert events[-1]["data"]["exemplar_ids"] == ["a-1", "c-1"]

    @pytest.mark.asyncio
    async def test_rejection_reasons_injected_into_write_prompt(self):
        feedback_repo = MagicMock()
        feedback_repo.get_recent_rejection_reasons.return_value = ["too dark", "no anime style"]
        llm = FakeLLMService(['["fox"]', "1. a", LONG_PROMPT])
        manager = make_manager(llm, feedback_repo=feedback_repo)

        await run_enhance(manager, n_candidates=1)

        write_prompt = llm.calls[-1]["messages"][0]["content"]
        assert "too dark" in write_prompt
        assert "no anime style" in write_prompt
        assert "avoid" in write_prompt.lower()

    @pytest.mark.asyncio
    async def test_write_uses_enhancement_guidelines_as_system(self):
        llm = FakeLLMService(['["fox"]', "1. a", LONG_PROMPT])
        manager = make_manager(llm)

        await run_enhance(manager, n_candidates=1)

        assert "seed, not a boundary" in llm.calls[-1]["system"]


class TestGroundingIncludesAdminPromptingGuidance:
    """`prompting_guidance` (admin-authored per-model text) must reach the
    write-stage prompt alongside the model's description, not be dropped."""

    @pytest.mark.asyncio
    async def test_prompting_guidance_included_in_write_prompt(self):
        llm = FakeLLMService(['["fox"]', "1. a", LONG_PROMPT])
        manager = make_manager(llm)
        manager.model_index_manager = Mock_ModelIndex(
            {"checkpoint.safetensors": {
                "id": "m-1",
                "filename": "checkpoint.safetensors",
                "description": "A photoreal SDXL checkpoint.",
                "prompting_guidance": "Always include a lens phrase like '35mm, f/1.8'.",
            }}
        )

        await run_enhance(
            manager, n_candidates=1,
            form_state={"form_data": {"checkpoint": "checkpoint.safetensors"}},
        )

        write_prompt = llm.calls[-1]["messages"][0]["content"]
        assert "Always include a lens phrase like '35mm, f/1.8'." in write_prompt
        assert "A photoreal SDXL checkpoint." in write_prompt
        # the admin guidance line precedes the model-notes line.
        assert write_prompt.index("lens phrase") < write_prompt.index("A photoreal SDXL checkpoint.")

    @pytest.mark.asyncio
    async def test_no_prompting_guidance_omits_the_line(self):
        llm = FakeLLMService(['["fox"]', "1. a", LONG_PROMPT])
        manager = make_manager(llm)
        manager.model_index_manager = Mock_ModelIndex(
            {"checkpoint.safetensors": {
                "id": "m-1", "filename": "checkpoint.safetensors", "description": "A checkpoint.",
            }}
        )

        await run_enhance(
            manager, n_candidates=1,
            form_state={"form_data": {"checkpoint": "checkpoint.safetensors"}},
        )

        write_prompt = llm.calls[-1]["messages"][0]["content"]
        assert "Admin prompting guidance" not in write_prompt


class TestPresetGuideThreading:
    """`llm.guide` (preset-authored family-level style grounding, see
    docs/presets.md "LLM context") reaches the write-stage prompt when a
    preset_manager is wired in and the active preset declares one."""

    @pytest.mark.asyncio
    async def test_preset_guide_included_when_preset_manager_wired(self):
        llm = FakeLLMService(['["fox"]', "1. a", LONG_PROMPT])
        preset_manager = MagicMock()
        preset_manager.file_repo.find_preset_by_id.return_value = SimpleNamespace(
            llm={"guide": "This model prefers short, comma-separated tags."},
        )
        manager = PromptEnhancementManager(
            llm_service=llm, model_index_manager=None, preset_manager=preset_manager,
        )

        await run_enhance(manager, n_candidates=1, form_state={"preset": "native/SDXL", "form_data": {}})

        write_prompt = llm.calls[-1]["messages"][0]["content"]
        assert "This model prefers short, comma-separated tags." in write_prompt
        preset_manager.file_repo.find_preset_by_id.assert_called_once_with("native/SDXL")

    @pytest.mark.asyncio
    async def test_no_preset_manager_omits_the_guide_and_never_raises(self):
        llm = FakeLLMService(['["fox"]', "1. a", LONG_PROMPT])
        manager = make_manager(llm)  # no preset_manager wired

        events = await run_enhance(
            manager, n_candidates=1, form_state={"preset": "native/SDXL", "form_data": {}},
        )

        assert events[-1]["data"]["candidates"][0]["text"] == LONG_PROMPT
        write_prompt = llm.calls[-1]["messages"][0]["content"]
        assert "House style guide" not in write_prompt

    @pytest.mark.asyncio
    async def test_preset_without_llm_block_omits_the_guide(self):
        llm = FakeLLMService(['["fox"]', "1. a", LONG_PROMPT])
        preset_manager = MagicMock()
        preset_manager.file_repo.find_preset_by_id.return_value = SimpleNamespace(llm=None)
        manager = PromptEnhancementManager(
            llm_service=llm, model_index_manager=None, preset_manager=preset_manager,
        )

        await run_enhance(manager, n_candidates=1, form_state={"preset": "native/SDXL", "form_data": {}})

        write_prompt = llm.calls[-1]["messages"][0]["content"]
        assert "House style guide" not in write_prompt

    @pytest.mark.asyncio
    async def test_mode_override_replaces_base_guide(self):
        llm = FakeLLMService(['["fox"]', "1. a", LONG_PROMPT])
        preset_manager = MagicMock()
        preset_manager.file_repo.find_preset_by_id.return_value = SimpleNamespace(
            llm={
                "guide": "Base guide: plain description.",
                "modes": {"refs": {"guide": "Refs guide: six-section brief."}},
            },
        )
        manager = PromptEnhancementManager(
            llm_service=llm, model_index_manager=None, preset_manager=preset_manager,
        )

        await run_enhance(
            manager, n_candidates=1,
            form_state={"preset": "native/H3", "mode": "refs", "form_data": {}},
        )

        write_prompt = llm.calls[-1]["messages"][0]["content"]
        assert "Refs guide: six-section brief." in write_prompt
        assert "Base guide: plain description." not in write_prompt

    @pytest.mark.asyncio
    async def test_mode_without_override_falls_back_to_base_guide(self):
        llm = FakeLLMService(['["fox"]', "1. a", LONG_PROMPT])
        preset_manager = MagicMock()
        preset_manager.file_repo.find_preset_by_id.return_value = SimpleNamespace(
            llm={
                "guide": "Base guide: plain description.",
                "modes": {"refs": {"guide": "Refs guide: six-section brief."}},
            },
        )
        manager = PromptEnhancementManager(
            llm_service=llm, model_index_manager=None, preset_manager=preset_manager,
        )

        await run_enhance(
            manager, n_candidates=1,
            form_state={"preset": "native/H3", "mode": "video", "form_data": {}},
        )

        write_prompt = llm.calls[-1]["messages"][0]["content"]
        assert "Base guide: plain description." in write_prompt
        assert "Refs guide: six-section brief." not in write_prompt


class Mock_ModelIndex:
    """Minimal model_index_manager double: model_repo.get_by_file_path -> lookup_model's shape."""

    def __init__(self, by_path):
        self._by_path = by_path
        self.model_repo = SimpleNamespace(
            get_by_file_path=self._get_by_file_path,
            get_all=lambda **kw: [],
        )

    def _get_by_file_path(self, path, **kwargs):
        data = self._by_path.get(path)
        if data is None:
            return None
        model = MagicMock()
        model.to_dict.return_value = {**data, "tags": []}
        return model


class TestAntiParroting:
    @pytest.mark.asyncio
    async def test_flat_candidate_retries_hotter(self):
        flat = "a fox in a forest"
        llm = FakeLLMService(['["fox"]', "1. a", flat, LONG_PROMPT])
        manager = make_manager(llm)

        events = await run_enhance(manager, n_candidates=1)

        # concepts + ideate + write + retry = 4 calls
        assert len(llm.calls) == 4
        assert llm.calls[3]["options"]["temperature"] == 1.0
        assert events[-1]["data"]["candidates"][0]["text"] == LONG_PROMPT

    @pytest.mark.asyncio
    async def test_rich_candidate_not_retried(self):
        llm = FakeLLMService(['["fox"]', "1. a", LONG_PROMPT])
        manager = make_manager(llm)

        await run_enhance(manager, n_candidates=1)

        assert len(llm.calls) == 3

    def test_is_flat_detects_word_overlap(self):
        brief = "a majestic dragon flying over mountain peaks during sunset"
        parrot = "majestic dragon flying over mountain peaks during sunset " * 8
        assert PromptEnhancementManager._is_flat(brief, parrot) is True
        assert PromptEnhancementManager._is_flat("a fox", LONG_PROMPT) is False


class TestParsing:
    def test_parse_json_array_lenient(self):
        parse = PromptEnhancementManager._parse_json_array
        assert parse('["a", "b"]') == ["a", "b"]
        assert parse('Here you go: ["a", "b"] hope it helps') == ["a", "b"]
        assert parse("no array here") == []
        assert parse("") == []

    def test_parse_numbered_list(self):
        parse = PromptEnhancementManager._parse_numbered_list
        assert parse("1. first idea\n2. second idea") == ["first idea", "second idea"]
        assert parse("1) first\n2) second") == ["first", "second"]
        assert parse("just one paragraph") == ["just one paragraph"]

    def test_strip_thinking(self):
        strip = PromptEnhancementManager._strip_thinking
        assert strip("<think>reasoning</think>actual output") == "actual output"
        assert strip("no thinking") == "no thinking"


class TestEnhanceWrapper:
    @pytest.mark.asyncio
    async def test_enhance_returns_final_result(self):
        llm = FakeLLMService(['["fox"]', "1. a", LONG_PROMPT])
        manager = make_manager(llm)

        result = await manager.enhance(user_id="u-1", llm_id="llm-1", brief="a fox", n_candidates=1)

        assert result["candidates"][0]["text"] == LONG_PROMPT
        assert result["brief"] == "a fox"
