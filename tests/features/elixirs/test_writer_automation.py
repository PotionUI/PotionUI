from types import SimpleNamespace

import pytest

from src.features.elixirs.generation_port import GenerationPort
from src.features.prompt_enhancement.collaborators import PromptEnhancementCollaborators

from tests.features.prompt_enhancement.test_write_prompt import FakeLLM
from tests.features.prompt_enhancement.test_write_prompt_automation import registry, subscribe

__all__ = ["registry"]


def port(llm, registry):
    writer = PromptEnhancementCollaborators(llm_service=llm, plugins=registry)
    return GenerationPort(
        users=None, orchestrator=None, broadcaster=None, generations=None, generation_files=None,
        history=None, file_service=None, media_store=None, prompt_writer=writer, storage_root=lambda: "",
    )


@pytest.mark.asyncio
async def test_an_elixir_writer_step_fires_the_llm_before_generate_automation(registry):
    enqueued = subscribe(registry, "llm.before_generate")
    llm = FakeLLM()
    user = SimpleNamespace(id="u-1", account_type=None)

    result = await port(llm, registry).write_prompt(user, {"instruction": "A fox", "media": []})

    assert result["text"]
    assert len(enqueued) == 1
    assert enqueued[0][2]["user_id"] == "u-1"
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_an_elixir_writer_step_fires_the_llm_after_generate_automation(registry):
    enqueued = subscribe(registry, "llm.after_generate")
    user = SimpleNamespace(id="u-1", account_type=None)

    await port(FakeLLM(), registry).write_prompt(user, {"instruction": "A fox", "media": []})

    assert len(enqueued) == 1
