from types import SimpleNamespace

import pytest

from src.features.llm.harness.events import Finish, FinishReason, ReasoningDelta, TextDelta
from src.features.llm.harness.messages import Origin
from src.features.llm.harness.adapter import Settings
from src.features.llm.harness.assembler import StepAssembler
from src.features.prompt_enhancement import operations
from src.features.prompt_enhancement.collaborators import PromptEnhancementCollaborators
from src.features.prompt_enhancement.operations import PromptWriteRefused


ORIGIN = Origin(adapter="a", provider="p", model="m")


def config(**overrides):
    values = {"id": "llm-1", "name": "Writer", "model": "writer-model", "supports_vision": True, "enabled": True}
    values.update(overrides)
    return SimpleNamespace(**values)


class Repository:
    def __init__(self, configs=None, default=None, assigned=()):
        self.configs = {item.id: item for item in configs or []}
        self.default = default
        self.assigned = {"u-1": list(assigned)}

    def get_configuration(self, llm_id):
        return self.configs.get(llm_id)

    def get_default_configuration(self):
        return self.configs.get(self.default) if self.default else None

    def get_user_llm_assignments(self, user_id):
        return self.assigned.get(user_id, [])


class FakeLLM:
    def __init__(self, reply="a red fox on a mossy rock, golden light", repository=None, thinking=()):
        self.reply = reply
        self.thinking = thinking
        self.calls = []
        self.repository = repository if repository is not None else Repository([config()], default="llm-1")

    async def complete(self, llm_id, messages, *, system=None, settings=None, tools=()):
        self.calls.append({"llm_id": llm_id, "messages": messages, "system": system, "settings": settings, "tools": tools})
        assembler = StepAssembler("msg-1", ORIGIN)
        for index, item in enumerate(self.thinking):
            assembler.feed(ReasoningDelta(block=index, text=item))
        assembler.feed(TextDelta(block=len(self.thinking), text=self.reply))
        assembler.feed(Finish(FinishReason.STOP))
        message = assembler.message()
        return SimpleNamespace(message=message, text=message.text())


class Presets:
    def __init__(self, preset):
        self.preset = preset

    def find_preset_by_id(self, preset_id):
        return self.preset if self.preset and self.preset.id == preset_id else None


def collaborators(llm, preset=None, prompt_db=None, assigned=("flux",), model_index=None):
    return PromptEnhancementCollaborators(
        llm_service=llm,
        prompt_database=prompt_db,
        model_index_manager=model_index,
        preset_collaborators=SimpleNamespace(
            file_repo=Presets(preset),
            db_repo=SimpleNamespace(get_available_preset_ids_for_user=lambda user_id: list(assigned)),
        )
        if preset
        else None,
    )


class ModelIndex:
    def __init__(self, models):
        self.models = models
        self.model_repo = SimpleNamespace(get_by_id=self.get_by_id)
        self.locator = SimpleNamespace(model_for_path=lambda path: None)

    def get_by_id(self, model_id, **kwargs):
        data = self.models.get(model_id)
        return SimpleNamespace(to_dict=lambda **kw: dict(data)) if data else None


def flux_preset():
    return SimpleNamespace(
        id="flux",
        name="Flux Dev",
        short_description="Photoreal text to image",
        description=None,
        llm={"guide": "Write natural sentences; lead with the subject.", "modes": {"edit": {"guide": "Say only what changes."}}},
    )


def sent_text(call):
    return "".join(part.text for part in call["messages"][0].parts if hasattr(part, "text") and part.text)


def sent_images(call):
    return [part.data for part in call["messages"][0].parts if type(part).__name__ == "ImagePart"]


@pytest.mark.asyncio
async def test_writes_with_preset_guide_summary_and_instruction():
    llm = FakeLLM()
    result = await operations.write_prompt(
        collaborators(llm, flux_preset()),
        "u-1",
        "Describe the attached photo as a prompt",
        form_state={"preset": "flux", "mode": "txt2img", "form_data": {}},
        images=["aGVsbG8="],
    )
    assert result == {"text": "a red fox on a mossy rock, golden light", "reasoning": None, "model_id": None, "llm_id": "llm-1"}
    call = llm.calls[0]
    text = sent_text(call)
    assert "Target preset: Flux Dev" in text
    assert "Mode: txt2img" in text
    assert "Photoreal text to image" in text
    assert "Write natural sentences; lead with the subject." in text
    assert "1 image is attached." in text
    assert text.index("Instruction:\nDescribe the attached photo as a prompt") > text.index("House style guide")
    assert sent_images(call) == ["aGVsbG8="]
    assert call["system"] == operations.WRITE_SYSTEM_MESSAGE


@pytest.mark.asyncio
async def test_mode_guide_replaces_the_preset_guide():
    llm = FakeLLM()
    await operations.write_prompt(
        collaborators(llm, flux_preset()), "u-1", "Make it night", form_state={"preset": "flux", "mode": "edit"},
    )
    text = sent_text(llm.calls[0])
    assert "Say only what changes." in text
    assert "lead with the subject" not in text


@pytest.mark.asyncio
async def test_model_exemplars_join_the_context_when_a_model_is_selected():
    llm = FakeLLM()
    searches = []

    async def search(**kwargs):
        searches.append(kwargs)
        if kwargs.get("source_provider") == operations.APPROVED_SOURCE_PROVIDER:
            return [SimpleNamespace(id="a", prompt="approved fox prompt", source_url=None, metadata={})]
        return [SimpleNamespace(id="c", prompt="community fox prompt", source_url="u")]

    index = ModelIndex({"m-9": {"id": "m-9", "filename": "flux.safetensors", "description": "Photoreal base", "tags": ["photo"]}})
    await operations.write_prompt(
        collaborators(llm, prompt_db=SimpleNamespace(search=search), model_index=index),
        "u-1",
        "A fox",
        form_state={"form_data": {"model": "model:m-9", "steps": 20}},
    )
    text = sent_text(llm.calls[0])
    assert "[user-approved] approved fox prompt" in text
    assert "[community] community fox prompt" in text
    assert "Generation model: flux.safetensors" in text
    assert "Photoreal base" in text
    assert [call["model_id"] for call in searches] == ["m-9", "m-9"]


@pytest.mark.asyncio
async def test_no_exemplar_search_without_a_model():
    llm = FakeLLM()

    async def search(**kwargs):
        raise AssertionError("searched without a model")

    await operations.write_prompt(collaborators(llm, prompt_db=SimpleNamespace(search=search)), "u-1", "A fox")
    assert "Instruction:\nA fox" in sent_text(llm.calls[0])


@pytest.mark.asyncio
async def test_images_need_a_vision_model():
    llm = FakeLLM(repository=Repository([config(supports_vision=False, name="Tiny")], default="llm-1"))
    with pytest.raises(PromptWriteRefused) as caught:
        await operations.write_prompt(collaborators(llm), "u-1", "Describe it", images=["aGVsbG8="])
    assert caught.value.code == "vision_not_supported"
    assert "Tiny cannot look at images" in str(caught.value)
    assert llm.calls == []


@pytest.mark.asyncio
async def test_without_a_default_the_users_assigned_model_writes():
    llm = FakeLLM(repository=Repository([config(id="b", name="Beta"), config(id="a", name="Alpha")], assigned=["b", "a"]))
    result = await operations.write_prompt(collaborators(llm), "u-1", "A fox")
    assert result["llm_id"] == "a"


@pytest.mark.asyncio
async def test_images_go_to_a_model_that_can_see_them():
    configs = [config(id="a", name="Alpha", supports_vision=False), config(id="b", name="Beta", supports_vision=True)]
    llm = FakeLLM(repository=Repository(configs, default="a", assigned=["b"]))
    result = await operations.write_prompt(collaborators(llm), "u-1", "Describe it", images=["aGVsbG8="])
    assert result["llm_id"] == "b"
    text_only = await operations.write_prompt(collaborators(llm), "u-1", "A fox")
    assert text_only["llm_id"] == "a"


@pytest.mark.asyncio
async def test_no_seeing_model_among_several_is_refused():
    configs = [config(id="a", name="Alpha", supports_vision=False), config(id="b", name="Beta", supports_vision=False)]
    llm = FakeLLM(repository=Repository(configs, assigned=["a", "b"]))
    with pytest.raises(PromptWriteRefused) as caught:
        await operations.write_prompt(collaborators(llm), "u-1", "Describe it", images=["aGVsbG8="])
    assert str(caught.value).startswith("None of the language models you can use can look at images.")


def test_disabled_assigned_models_are_skipped():
    llm = FakeLLM(repository=Repository([config(id="a", enabled=False), config(id="b", name="Beta")], assigned=["a", "b"]))
    assert operations.writer_config(collaborators(llm), "u-1").id == "b"


@pytest.mark.asyncio
async def test_text_only_works_without_vision():
    llm = FakeLLM(repository=Repository([config(supports_vision=False)], default="llm-1"))
    result = await operations.write_prompt(collaborators(llm), "u-1", "A fox at dawn")
    assert result["text"].startswith("a red fox")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repository", "code"),
    [
        (Repository(), "no_llm"),
        (Repository([config(enabled=False)], default="llm-1"), "no_llm"),
    ],
)
async def test_missing_or_disabled_llm_is_refused(repository, code):
    with pytest.raises(PromptWriteRefused) as caught:
        await operations.write_prompt(collaborators(FakeLLM(repository=repository)), "u-1", "A fox")
    assert caught.value.code == code


@pytest.mark.asyncio
async def test_an_explicit_disabled_llm_is_named():
    llm = FakeLLM(repository=Repository([config(enabled=False, name="Old")], default="llm-1"))
    with pytest.raises(PromptWriteRefused) as caught:
        await operations.write_prompt(collaborators(llm), "u-1", "A fox", llm_id="llm-1")
    assert caught.value.code == "llm_disabled"
    assert str(caught.value) == "The language model Old is turned off."


@pytest.mark.asyncio
async def test_an_explicit_llm_id_is_used():
    llm = FakeLLM(repository=Repository([config(), config(id="llm-2", name="Other")], default="llm-1", assigned=["llm-2"]))
    result = await operations.write_prompt(collaborators(llm), "u-1", "A fox", llm_id="llm-2")
    assert result["llm_id"] == "llm-2"
    assert llm.calls[0]["llm_id"] == "llm-2"


@pytest.mark.asyncio
async def test_blank_instruction_is_refused():
    with pytest.raises(PromptWriteRefused) as caught:
        await operations.write_prompt(collaborators(FakeLLM()), "u-1", "   ")
    assert caught.value.code == "no_instruction"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("```\na fox\n```", "a fox"),
        ("Prompt: a fox", "a fox"),
        ('"a fox"', "a fox"),
        ("  a fox  ", "a fox"),
    ],
)
async def test_reply_wrapping_is_removed(reply, expected):
    result = await operations.write_prompt(collaborators(FakeLLM(reply=reply)), "u-1", "A fox")
    assert result["text"] == expected


@pytest.mark.asyncio
async def test_an_empty_reply_is_refused():
    with pytest.raises(PromptWriteRefused) as caught:
        await operations.write_prompt(collaborators(FakeLLM(reply="  ")), "u-1", "A fox")
    assert caught.value.code == "empty_reply"


def test_writer_status_reports_vision_and_absence():
    vision = operations.writer_status(collaborators(FakeLLM()), "u-1")
    assert vision == {
        "available": True,
        "vision": True,
        "name": "Writer",
        "code": None,
        "message": None,
        "models": [{"id": "llm-1", "name": "Writer", "vision": True}],
    }
    missing = operations.writer_status(collaborators(FakeLLM(repository=Repository())), "u-1")
    assert missing["available"] is False
    assert missing["code"] == "no_llm"


@pytest.mark.asyncio
async def test_a_preset_the_user_cannot_see_is_refused_before_anything_is_read():
    llm = FakeLLM()
    preset = flux_preset()
    looked = []
    collab = collaborators(llm, preset, assigned=())
    original = collab.preset_collaborators.file_repo.find_preset_by_id
    collab.preset_collaborators.file_repo.find_preset_by_id = lambda preset_id: looked.append(preset_id) or original(preset_id)
    with pytest.raises(PromptWriteRefused) as caught:
        await operations.write_prompt(collab, "u-1", "A fox", form_state={"preset": "flux", "mode": "txt2img"})
    assert caught.value.code == "preset_not_available"
    assert looked == [] and llm.calls == []


@pytest.mark.asyncio
async def test_an_admin_sees_presets_that_are_not_assigned():
    llm = FakeLLM()
    await operations.write_prompt(
        collaborators(llm, flux_preset(), assigned=()), "u-1", "A fox", form_state={"preset": "flux"}, is_admin=True,
    )
    assert "Target preset: Flux Dev" in sent_text(llm.calls[0])


@pytest.mark.asyncio
async def test_a_long_instruction_and_guide_are_cut_to_their_caps():
    llm = FakeLLM()
    preset = flux_preset()
    preset.llm = {"guide": "g" * (operations.MAX_GUIDE_CHARS + 500)}
    await operations.write_prompt(
        collaborators(llm, preset), "u-1", "i" * (operations.MAX_INSTRUCTION_CHARS + 500), form_state={"preset": "flux"},
    )
    text = sent_text(llm.calls[0])
    assert "g" * operations.MAX_GUIDE_CHARS in text and "g" * (operations.MAX_GUIDE_CHARS + 1) not in text
    assert "i" * operations.MAX_INSTRUCTION_CHARS in text and "i" * (operations.MAX_INSTRUCTION_CHARS + 1) not in text


@pytest.mark.asyncio
async def test_an_explicit_llm_the_user_may_not_use_is_refused():
    llm = FakeLLM(repository=Repository([config(), config(id="llm-2", name="Other")], default="llm-1"))
    with pytest.raises(PromptWriteRefused) as caught:
        await operations.write_prompt(collaborators(llm), "u-1", "A fox", llm_id="llm-2")
    assert caught.value.code == "llm_not_available"
    assert llm.calls == []
    status = operations.writer_status(collaborators(llm), "u-1", "llm-2")
    assert status["available"] is False
    assert status["code"] == "llm_not_available"


@pytest.mark.asyncio
async def test_the_default_model_can_be_named_explicitly():
    llm = FakeLLM()
    result = await operations.write_prompt(collaborators(llm), "u-1", "A fox", llm_id="llm-1")
    assert result["llm_id"] == "llm-1"


@pytest.mark.asyncio
async def test_an_explicit_model_without_vision_is_not_swapped_for_another():
    llm = FakeLLM(
        repository=Repository([config(supports_vision=False), config(id="llm-2", name="Seer")], default="llm-1", assigned=["llm-2"])
    )
    with pytest.raises(PromptWriteRefused) as caught:
        await operations.write_prompt(collaborators(llm), "u-1", "Describe it", llm_id="llm-1", images=["aGVsbG8="])
    assert caught.value.code == "vision_not_supported"


@pytest.mark.asyncio
async def test_the_reasoning_comes_back_with_the_prompt_and_the_call_follows_the_model_configuration():
    llm = FakeLLM(thinking=("Look at the subject.", "Keep it short."))
    result = await operations.write_prompt(collaborators(llm), "u-1", "A fox")
    assert result["reasoning"] == "Look at the subject.\n\nKeep it short."
    assert llm.calls[0]["settings"] == Settings()
    assert llm.calls[0]["tools"] == ()


@pytest.mark.asyncio
async def test_no_reasoning_is_none():
    result = await operations.write_prompt(collaborators(FakeLLM()), "u-1", "A fox")
    assert result["reasoning"] is None


def test_writer_status_lists_the_default_then_the_assigned_models():
    llm = FakeLLM(
        repository=Repository(
            [config(), config(id="b", name="Beta", supports_vision=False), config(id="a", name="Alpha"), config(id="off", name="Off", enabled=False)],
            default="llm-1",
            assigned=["b", "a", "off"],
        )
    )
    status = operations.writer_status(collaborators(llm), "u-1")
    assert [(item["id"], item["vision"]) for item in status["models"]] == [("llm-1", True), ("a", True), ("b", False)]


def test_writer_status_for_a_forbidden_model_still_lists_what_is_allowed():
    llm = FakeLLM(repository=Repository([config(), config(id="x", name="X")], default="llm-1"))
    status = operations.writer_status(collaborators(llm), "u-1", "x")
    assert status["code"] == "llm_not_available"
    assert [item["id"] for item in status["models"]] == ["llm-1"]


def test_writer_status_for_a_deleted_model_is_not_available():
    status = operations.writer_status(collaborators(FakeLLM()), "u-1", "gone")
    assert status["code"] == "llm_not_available"


class HookPlugins:
    def __init__(self, events, mutate=None, block=None):
        self.events = events
        self.mutate = mutate or {}
        self.block = block

    def execute_hook(self, hook, initial_data):
        self.events.append((str(hook), dict(initial_data)))
        data = dict(initial_data)
        if str(hook).endswith("before_generate"):
            data.update(self.mutate)
            if self.block:
                data.update({"blocked": True, "block_reason": self.block})
        return SimpleNamespace(data=data), None


def with_plugins(base, plugins):
    from dataclasses import replace

    return replace(base, plugins=plugins)


class OrderedLLM(FakeLLM):
    def __init__(self, events, **kwargs):
        super().__init__(**kwargs)
        self.events = events

    async def complete(self, *args, **kwargs):
        self.events.append(("complete", {}))
        return await super().complete(*args, **kwargs)


@pytest.mark.asyncio
async def test_generate_hooks_fire_around_the_call_with_the_sent_prompt():
    events = []
    llm = OrderedLLM(events)
    await operations.write_prompt(
        with_plugins(collaborators(llm), HookPlugins(events)), "u-1", "a fox", images=["aGVsbG8="],
    )
    names = [name for name, _ in events]
    assert [name.rsplit(".", 1)[-1] for name in names] == ["before_generate", "complete", "after_generate"]
    before = events[0][1]
    assert before["user_id"] == "u-1"
    assert before["config_id"] == "llm-1"
    assert before["has_image"] is True
    assert before["prompt"] == sent_text(llm.calls[0])
    after = events[2][1]
    assert after["content"] == "a red fox on a mossy rock, golden light"
    assert after["config_id"] == "llm-1"


@pytest.mark.asyncio
async def test_a_hook_veto_stops_the_call_and_surfaces_as_blocked():
    events = []
    llm = OrderedLLM(events)
    with pytest.raises(PromptWriteRefused) as raised:
        await operations.write_prompt(
            with_plugins(collaborators(llm), HookPlugins(events, block="VRAM is busy")), "u-1", "a fox",
        )
    assert raised.value.code == "blocked"
    assert str(raised.value) == "VRAM is busy"
    assert llm.calls == []
    assert [name.rsplit(".", 1)[-1] for name, _ in events] == ["before_generate"]


@pytest.mark.asyncio
async def test_a_hook_prompt_rewrite_is_what_the_model_receives():
    events = []
    llm = OrderedLLM(events)
    await operations.write_prompt(
        with_plugins(collaborators(llm), HookPlugins(events, mutate={"prompt": "rewritten by plugin"})), "u-1", "a fox",
    )
    assert sent_text(llm.calls[0]) == "rewritten by plugin"


class WaitingPlugins:
    def __init__(self, waits):
        self.waits = waits

    def execute_hook(self, hook, initial_data):
        data = dict(initial_data)
        if str(hook).endswith("before_generate"):
            data["__hook_blocking_waits__"] = list(self.waits)
        return SimpleNamespace(data=data), None


@pytest.mark.asyncio
async def test_blocking_hook_runs_finish_before_the_model_is_called():
    import asyncio

    events = []

    async def slow_run():
        await asyncio.sleep(0.01)
        events.append(("wait-done", {}))

    llm = OrderedLLM(events)
    await operations.write_prompt(with_plugins(collaborators(llm), WaitingPlugins([slow_run()])), "u-1", "a fox")
    assert [name for name, _ in events] == ["wait-done", "complete"]

@pytest.mark.parametrize(
    "assigned,is_admin,visible",
    [((), False, False), (("flux",), False, True), ((), True, True)],
    ids=["unassigned-user", "assigned-user", "admin"],
)
def test_preset_visible_follows_the_assignment_and_admins_see_all(assigned, is_admin, visible):
    collab = collaborators(FakeLLM(), flux_preset(), assigned=assigned)

    assert operations.preset_visible(collab, "u-1", "flux", is_admin) is visible
