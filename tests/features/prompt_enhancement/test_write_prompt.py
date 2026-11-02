from types import SimpleNamespace

import pytest

from src.features.prompt_enhancement import operations
from src.features.prompt_enhancement.collaborators import PromptEnhancementCollaborators
from src.features.prompt_enhancement.operations import PromptWriteRefused


def config(**overrides):
    values = {"id": "llm-1", "name": "Writer", "supports_vision": True, "enabled": True}
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
    def __init__(self, reply="a red fox on a mossy rock, golden light", repository=None):
        self.reply = reply
        self.calls = []
        self.repository = repository if repository is not None else Repository([config()], default="llm-1")

    async def complete(self, llm_id, messages, *, system=None, settings=None, tools=()):
        self.calls.append({"llm_id": llm_id, "messages": messages, "system": system, "settings": settings})
        return SimpleNamespace(text=self.reply)


class Presets:
    def __init__(self, preset):
        self.preset = preset

    def find_preset_by_id(self, preset_id):
        return self.preset if self.preset and self.preset.id == preset_id else None


def collaborators(llm, preset=None, prompt_db=None):
    return PromptEnhancementCollaborators(
        llm_service=llm,
        prompt_database=prompt_db,
        preset_collaborators=SimpleNamespace(file_repo=Presets(preset)) if preset else None,
    )


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
    assert result == {"text": "a red fox on a mossy rock, golden light", "model_id": None, "llm_id": "llm-1"}
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
async def test_model_exemplars_join_the_context_when_a_model_is_selected(monkeypatch):
    llm = FakeLLM()
    searches = []

    async def search(**kwargs):
        searches.append(kwargs)
        if kwargs.get("source_provider") == operations.APPROVED_SOURCE_PROVIDER:
            return [SimpleNamespace(id="a", prompt="approved fox prompt", source_url=None, metadata={})]
        return [SimpleNamespace(id="c", prompt="community fox prompt", source_url="u")]

    monkeypatch.setattr(operations, "resolve_active_model_id", lambda form_state, index: "m-9")
    monkeypatch.setattr(operations, "_gather_model_grounding", lambda c, f: {"filename": "flux.safetensors", "tags": ["photo"]})
    await operations.write_prompt(
        collaborators(llm, prompt_db=SimpleNamespace(search=search)), "u-1", "A fox", form_state={"form_data": {}},
    )
    text = sent_text(llm.calls[0])
    assert "[user-approved] approved fox prompt" in text
    assert "[community] community fox prompt" in text
    assert "Generation model: flux.safetensors" in text
    assert all(call["model_id"] == "m-9" for call in searches)


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
    llm = FakeLLM(repository=Repository([config(enabled=False, name="Old")]))
    with pytest.raises(PromptWriteRefused) as caught:
        await operations.write_prompt(collaborators(llm), "u-1", "A fox", llm_id="llm-1")
    assert caught.value.code == "llm_disabled"
    assert str(caught.value) == "The language model Old is turned off."


@pytest.mark.asyncio
async def test_an_explicit_llm_id_is_used():
    llm = FakeLLM(repository=Repository([config(), config(id="llm-2", name="Other")], default="llm-1"))
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
    assert vision == {"available": True, "vision": True, "name": "Writer", "code": None, "message": None}
    missing = operations.writer_status(collaborators(FakeLLM(repository=Repository())), "u-1")
    assert missing["available"] is False
    assert missing["code"] == "no_llm"
