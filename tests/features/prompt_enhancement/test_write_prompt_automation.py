import asyncio

import pytest

from src.features.automation.triggers.hook_bridge import HookEventBridge, HookEventTrigger
from src.features.prompt_enhancement import operations
from src.features.prompt_enhancement.collaborators import PromptEnhancementCollaborators
from src.platform.plugins.registry import PluginRegistry

from tests.features.prompt_enhancement.test_write_prompt import FakeLLM


@pytest.fixture
def registry(tmp_path):
    marketplace = tmp_path / "marketplace"
    local = tmp_path / "local"
    marketplace.mkdir()
    local.mkdir()
    return PluginRegistry(str(marketplace), str(local))


class OrderedLLM(FakeLLM):
    def __init__(self, log):
        super().__init__()
        self.log = log

    async def complete(self, *args, **kwargs):
        self.log.append("complete")
        return await super().complete(*args, **kwargs)


def subscribe(registry, hook_name, config=None, schedule_run=None):
    enqueued = []
    bridge = HookEventBridge(registry.hook_chain)
    trigger = HookEventTrigger(
        "auto-1",
        "node-1",
        {"hook_name": hook_name, **(config or {})},
        lambda automation_id, node_id, payload: enqueued.append((automation_id, node_id, payload)),
        bridge,
        schedule_run=schedule_run,
    )
    bridge.subscribe(hook_name, trigger)
    return enqueued


def writer(llm, registry):
    return PromptEnhancementCollaborators(llm_service=llm, plugins=registry)


@pytest.mark.asyncio
async def test_writer_llm_call_fires_a_before_generate_automation(registry):
    enqueued = subscribe(registry, "llm.before_generate")
    llm = FakeLLM()

    await operations.write_prompt(writer(llm, registry), "u-1", "A fox")

    assert len(enqueued) == 1
    automation_id, node_id, payload = enqueued[0]
    assert (automation_id, node_id) == ("auto-1", "node-1")
    assert payload["user_id"] == "u-1"
    assert payload["config_id"] == "llm-1"
    assert payload["has_image"] is False
    assert "A fox" in payload["prompt"]
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_writer_llm_call_fires_an_after_generate_automation(registry):
    enqueued = subscribe(registry, "llm.after_generate")

    await operations.write_prompt(writer(FakeLLM(), registry), "u-1", "A fox")

    assert len(enqueued) == 1
    assert enqueued[0][2]["content"] == "a red fox on a mossy rock, golden light"
    assert enqueued[0][2]["config_id"] == "llm-1"


@pytest.mark.asyncio
async def test_writer_reports_attached_images_to_the_automation(registry):
    enqueued = subscribe(registry, "llm.before_generate")

    await operations.write_prompt(writer(FakeLLM(), registry), "u-1", "Describe it", images=["aGVsbG8="])

    assert enqueued[0][2]["has_image"] is True


@pytest.mark.asyncio
async def test_writer_holds_the_llm_call_until_a_waiting_automation_completes(registry):
    log = []
    future = asyncio.get_running_loop().create_future()

    def schedule_run(automation_id, node_id, payload):
        log.append("scheduled")
        return future

    subscribe(registry, "llm.before_generate", {"wait_for_completion": True}, schedule_run)
    llm = OrderedLLM(log)

    task = asyncio.create_task(operations.write_prompt(writer(llm, registry), "u-1", "A fox"))
    for _ in range(20):
        await asyncio.sleep(0)

    assert log == ["scheduled"]
    assert not task.done()

    future.set_result(None)
    await task

    assert log == ["scheduled", "complete"]


@pytest.mark.asyncio
async def test_writer_proceeds_after_the_wait_timeout_when_the_automation_never_finishes(registry):
    log = []
    future = asyncio.get_running_loop().create_future()
    subscribe(
        registry,
        "llm.before_generate",
        {"wait_for_completion": True, "wait_timeout_s": 0.05},
        lambda automation_id, node_id, payload: future,
    )
    llm = OrderedLLM(log)

    result = await asyncio.wait_for(operations.write_prompt(writer(llm, registry), "u-1", "A fox"), timeout=5)

    assert log == ["complete"]
    assert result["text"] == "a red fox on a mossy rock, golden light"
    assert not future.done()


@pytest.mark.asyncio
async def test_writer_without_plugins_still_writes():
    llm = FakeLLM()

    result = await operations.write_prompt(PromptEnhancementCollaborators(llm_service=llm), "u-1", "A fox")

    assert result["llm_id"] == "llm-1"
