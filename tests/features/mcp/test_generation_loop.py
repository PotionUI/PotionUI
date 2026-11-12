import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.features.api_keys.repository import ApiKeyRepository
from src.features.generation.exceptions import GenerationNotFoundException
from src.features.generation.status_tracker import GenerationState
from src.features.llm.tools.base import ToolServices
from src.features.llm.tools.builtin import generation_loop_tools as loop_tools
from src.features.llm.tools.builtin import register_builtin_tools
from src.features.llm.tools.builtin.generation_loop import plain_step
from src.features.llm.tools.registry import ToolRegistry
from src.features.media.dto import MediaResult
from src.features.media.dto import UploadResult
from src.features.mcp.protocol import JsonRpcError
from src.features.mcp.tool_policy import POLICY_ALL_ON, POLICY_READ_ONLY_ON
from src.features.mcp.tool_repository import McpToolRepository
from tests.features.generation import test_orchestrator_queue as queue_fakes
from tests.features.mcp.support import KEY_ID, api_key, build_collaborators, handle_method
from tests.fixtures.seed import seed_user

_bind_form_passthrough = queue_fakes._bind_form_passthrough
backends = queue_fakes.backends
orchestrator = queue_fakes.orchestrator
repo = queue_fakes.repo

OWNER = "user_1"
OTHER = "user_2"
LOOP_READS = ("get_generation_status", "wait_for_generation", "get_generation_results")
LOOP_WRITES = ("cancel_generation", "upload_media")

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


def b64(data):
    return base64.b64encode(data).decode("ascii")


class FakeRepo:
    def __init__(self):
        self.rows = {}

    def get_by_id(self, generation_id):
        return self.rows.get(generation_id)


class FakeHistory:
    def __init__(self):
        self.generation_repo = FakeRepo()
        self.details = {}

    async def get_by_id_async(self, generation_id, user_id, include_files=True, include_batch=False):
        detail = self.details.get(generation_id)
        if detail is None or detail["user_id"] != user_id:
            raise GenerationNotFoundException(generation_id)
        return detail


class FakeStore:
    def __init__(self):
        self.fetched = []
        self.sizes = {"large": 900_000, "medium": 400_000, "small": 100_000}
        self.upload_media = AsyncMock(side_effect=self._upload)
        self.check_upload_size = Mock()

    def get_file_by_id(self, file_id, size=None):
        self.fetched.append((file_id, size))
        return MediaResult(content=b"x" * self.sizes[size], media_type="image/webp")

    async def _upload(self, file_data, filename, content_type, user_id, purpose):
        return UploadResult(
            path=f"/srv/storage/uploads/{filename}", relative_path=f"uploads/stored-{filename}",
            filename=f"stored-{filename}", size=len(file_data), url="/u", id="up-1", width=8, height=8,
        )


def completed_detail(files, user_id=OWNER, parameters=None):
    return {
        "id": "gen_1", "user_id": user_id, "preset_id": "p", "mode": "txt2img", "seed": 7,
        "parameters": parameters if parameters is not None else {
            "seed": [111, 222], "positive_prompt": ["a red fox", "a blue fox"], "negative_prompt": ["blur"],
        },
        "files": files,
    }


def image_file(file_id, final=True):
    return {
        "id": file_id, "file_type": "image", "mime_type": "image/png", "is_final": final,
        "file_path": f"generations/2026-11-12/gen_1/{file_id}.png", "width": 512, "height": 512,
    }


def video_file(file_id):
    return {
        "id": file_id, "file_type": "video", "mime_type": "video/mp4", "is_final": True,
        "file_path": f"generations/2026-11-12/gen_1/{file_id}.mp4", "duration_seconds": 3.0,
    }


@pytest.fixture
def history():
    return FakeHistory()


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def collaborators(mcp_db, orchestrator, history, store, repo):
    for user_id in (OWNER, OTHER):
        seed_user(mcp_db, user_id)
        McpToolRepository().apply_default(user_id, POLICY_ALL_ON, {})
    ApiKeyRepository().create(api_key(KEY_ID, OWNER))
    registry = ToolRegistry()
    register_builtin_tools(registry)
    llm_repository = Mock()
    llm_repository.get_default_configuration.return_value = None
    settings = Mock()
    settings.get_setting.return_value = "https://potion.test"
    return build_collaborators(registry, ToolServices(
        llm_repository=llm_repository,
        generation_orchestrator=orchestrator,
        generation_history_facade=history,
        media_store=store,
        settings=settings,
    ))


async def call(collaborators, name, arguments, user_id=OWNER, key_id=KEY_ID):
    return await handle_method(
        collaborators, "tools/call", {"name": name, "arguments": arguments}, user_id, key_id=key_id,
    )


def payload(result):
    return json.loads(result["content"][0]["text"])


async def start(orchestrator, generation_id, tab="tab_a"):
    await queue_fakes._start(orchestrator, tab, generation_id)


async def finish(orchestrator, generation_id, state=GenerationState.COMPLETED):
    await orchestrator.status_tracker.transition_async(generation_id, state)


async def names_listed(collaborators, user_id=OWNER, key_id=KEY_ID):
    listed = await handle_method(collaborators, "tools/list", {}, user_id, key_id=key_id)
    return {tool["name"] for tool in listed["tools"]}


class TestLoopToolsPolicyLevels:
    @pytest.mark.asyncio
    async def test_every_loop_tool_is_listed_when_everything_is_on(self, collaborators):
        assert set(LOOP_READS + LOOP_WRITES) <= await names_listed(collaborators)

    @pytest.mark.asyncio
    async def test_a_read_only_user_default_keeps_the_reads_and_drops_cancel_and_upload(self, collaborators):
        McpToolRepository().apply_default(OWNER, POLICY_READ_ONLY_ON, {})

        names = await names_listed(collaborators)

        assert set(LOOP_READS) <= names
        assert names.isdisjoint(LOOP_WRITES)

    @pytest.mark.asyncio
    async def test_a_read_only_ceiling_drops_cancel_and_upload_whatever_the_user_chose(self, collaborators):
        collaborators.settings.set_setting("mcp_tools_ceiling_policy", "read_only_on")

        names = await names_listed(collaborators)

        assert set(LOOP_READS) <= names
        assert names.isdisjoint(LOOP_WRITES)

    @pytest.mark.asyncio
    async def test_the_ceiling_can_turn_one_loop_tool_off(self, collaborators):
        McpToolRepository().apply_ceiling({"get_generation_results": False}, None)

        names = await names_listed(collaborators)

        assert "get_generation_results" not in names
        assert "get_generation_status" in names

    @pytest.mark.asyncio
    async def test_a_key_can_switch_a_loop_tool_off(self, collaborators):
        McpToolRepository().apply_key_overrides(KEY_ID, ["cancel_generation"], [])

        assert "cancel_generation" not in await names_listed(collaborators)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", LOOP_READS + LOOP_WRITES)
    async def test_a_tool_the_ceiling_turned_off_is_refused_when_called(self, collaborators, name):
        McpToolRepository().apply_ceiling({name: False}, None)

        with pytest.raises(JsonRpcError, match="turned off by your administrator"):
            await call(collaborators, name, {"generation_id": "gen_1"})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", LOOP_READS + LOOP_WRITES)
    async def test_a_tool_the_key_turned_off_is_refused_when_called(self, collaborators, name):
        McpToolRepository().apply_key_overrides(KEY_ID, [name], [])

        with pytest.raises(JsonRpcError, match="turned off for this key"):
            await call(collaborators, name, {"generation_id": "gen_1"})


class TestLoopToolsDeclaredFlags:
    def test_reads_neither_write_nor_spend_and_cancel_and_upload_write_without_spending(self):
        registry = ToolRegistry()
        register_builtin_tools(registry)

        flags = {name: (registry.get(name).writes, registry.get(name).spends) for name in LOOP_READS + LOOP_WRITES}

        assert flags == {
            "get_generation_status": (False, False),
            "wait_for_generation": (False, False),
            "get_generation_results": (False, False),
            "cancel_generation": (True, False),
            "upload_media": (True, False),
        }


class TestLoopGetGenerationStatus:
    @pytest.mark.asyncio
    async def test_a_running_generation_reports_its_state_for_its_owner(self, collaborators, orchestrator):
        await start(orchestrator, "gen_1")

        result = await call(collaborators, "get_generation_status", {"generation_id": "gen_1"})

        assert result["isError"] is False
        body = payload(result)
        assert (body["generation_id"], body["status"], body["finished"]) == ("gen_1", "running", False)
        assert "queue_position" not in body

    @pytest.mark.asyncio
    async def test_a_queued_generation_reports_its_place_in_the_queue(self, collaborators, orchestrator):
        await start(orchestrator, "gen_1")
        await start(orchestrator, "gen_2", tab="tab_b")

        body = payload(await call(collaborators, "get_generation_status", {"generation_id": "gen_2"}))

        assert (body["status"], body["queue_position"], body["queued_for_you"]) == ("pending", 0, 1)

    @pytest.mark.asyncio
    async def test_progress_and_the_step_are_given_in_plain_words(self, collaborators, orchestrator):
        await start(orchestrator, "gen_1")
        record = orchestrator.status_tracker.get("gen_1")
        record.progress = 0.4
        record.current_step = "<<PIPE:generator:bolt>> <<MODEL:flux-dev.safetensors>> <<PROGRESS:12/30>>"

        body = payload(await call(collaborators, "get_generation_status", {"generation_id": "gen_1"}))

        assert body["progress_percent"] == 40
        assert body["step"] == "Generating, step 12/30"
        assert "flux" not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_a_failed_generation_carries_a_user_facing_error_not_the_raw_one(self, collaborators, orchestrator):
        await start(orchestrator, "gen_1")
        record = orchestrator.status_tracker.get("gen_1")
        record.message = "CUDA out of memory at /srv/models/secret/flux.safetensors"
        record.error_code = "cuda_oom"
        await finish(orchestrator, "gen_1", GenerationState.FAILED)

        body = payload(await call(collaborators, "get_generation_status", {"generation_id": "gen_1"}))

        assert body["status"] == "failed" and body["finished"] is True
        assert body["error"]
        assert "/srv/models" not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_a_finished_generation_that_left_memory_is_read_from_history(self, collaborators, history):
        history.generation_repo.rows["gen_old"] = SimpleNamespace(
            user_id=OWNER, to_dict=lambda: {"id": "gen_old", "status": "completed", "progress": 1.0, "preset_id": "p"},
        )

        body = payload(await call(collaborators, "get_generation_status", {"generation_id": "gen_old"}))

        assert (body["status"], body["finished"], body["progress_percent"]) == ("completed", True, 100)

    @pytest.mark.asyncio
    async def test_another_users_generation_looks_like_it_does_not_exist(self, collaborators, orchestrator):
        await start(orchestrator, "gen_1")

        result = await call(collaborators, "get_generation_status", {"generation_id": "gen_1"}, user_id=OTHER)

        assert result["isError"] is True
        assert "No generation 'gen_1' of yours" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_another_users_stored_generation_looks_like_it_does_not_exist(self, collaborators, history):
        history.generation_repo.rows["gen_old"] = SimpleNamespace(
            user_id=OWNER, to_dict=lambda: {"id": "gen_old", "status": "completed"},
        )

        result = await call(collaborators, "get_generation_status", {"generation_id": "gen_old"}, user_id=OTHER)

        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_an_unknown_id_is_not_found(self, collaborators):
        result = await call(collaborators, "get_generation_status", {"generation_id": "nope"})

        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_a_missing_id_is_refused(self, collaborators):
        result = await call(collaborators, "get_generation_status", {})

        assert (result["isError"], result["content"][0]["text"]) == (True, "Error: 'generation_id' is required")


class TestLoopPlainStep:
    def test_a_user_never_sees_model_names_or_unknown_pipes(self):
        assert plain_step("<<PIPE:acme_secret:bolt>> <<MODEL:flux.safetensors>>", False) == "Working"

    def test_an_administrator_gets_the_step_without_its_markers(self):
        assert plain_step("Loading <<MODEL:flux.safetensors>> <<PROGRESS:3/30>>", True) == "Loading flux.safetensors 3/30"

    def test_nothing_in_gives_nothing_out(self):
        assert plain_step(None, False) is None


class TestLoopCancelGeneration:
    @pytest.mark.asyncio
    async def test_a_queued_generation_is_cancelled_without_touching_the_backend(self, collaborators, orchestrator, backends):
        await start(orchestrator, "gen_1")
        await start(orchestrator, "gen_2", tab="tab_b")

        result = await call(collaborators, "cancel_generation", {"generation_id": "gen_2"})

        assert result["isError"] is False
        assert payload(result)["status"] == "cancelled"
        backends["native"].cancel_generation.assert_not_awaited()
        assert orchestrator.status_tracker.get("gen_2").state == GenerationState.CANCELLED
        assert orchestrator.status_tracker.get("gen_1").state == GenerationState.RUNNING

    @pytest.mark.asyncio
    async def test_a_running_generation_is_cancelled_on_its_backend(self, collaborators, orchestrator, backends):
        backends["native"].take_cancel_notice.return_value = "Stopped; the cloud may still bill the part that ran."
        await start(orchestrator, "gen_1")

        result = await call(collaborators, "cancel_generation", {"generation_id": "gen_1"})

        assert result["isError"] is False
        assert payload(result)["cancel_notice"] == "Stopped; the cloud may still bill the part that ran."
        backends["native"].cancel_generation.assert_awaited_once_with("gen_1")
        assert orchestrator.status_tracker.get("gen_1").state == GenerationState.CANCELLED

    @pytest.mark.asyncio
    async def test_the_cancel_is_announced_to_the_open_pages(self, collaborators, orchestrator):
        orchestrator.output_broadcaster = Mock()
        orchestrator.output_broadcaster.broadcast_cancelled = AsyncMock()
        await start(orchestrator, "gen_1")

        await call(collaborators, "cancel_generation", {"generation_id": "gen_1"})

        orchestrator.output_broadcaster.broadcast_cancelled.assert_awaited_once_with("gen_1")

    @pytest.mark.asyncio
    async def test_someone_elses_generation_is_not_cancelled(self, collaborators, orchestrator, backends):
        await start(orchestrator, "gen_1")

        result = await call(collaborators, "cancel_generation", {"generation_id": "gen_1"}, user_id=OTHER)

        assert result["isError"] is True
        backends["native"].cancel_generation.assert_not_awaited()
        assert orchestrator.status_tracker.get("gen_1").state == GenerationState.RUNNING

    @pytest.mark.asyncio
    async def test_a_finished_generation_says_there_is_nothing_to_cancel(self, collaborators, orchestrator):
        await start(orchestrator, "gen_1")
        await finish(orchestrator, "gen_1")

        result = await call(collaborators, "cancel_generation", {"generation_id": "gen_1"})

        assert result["isError"] is True
        assert "already completed" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_a_backend_that_cannot_cancel_is_reported_not_hidden(self, collaborators, orchestrator, backends):
        await start(orchestrator, "gen_1")
        backends["native"].cancel_generation.return_value = False
        orchestrator._backend_may_still_run = Mock(return_value=True)

        result = await call(collaborators, "cancel_generation", {"generation_id": "gen_1"})

        assert result["isError"] is True
        assert "could not be cancelled" in result["content"][0]["text"]


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(loop_tools.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(loop_tools.asyncio, "sleep", fake.sleep)
    return fake


class TestLoopWaitForGeneration:
    @pytest.mark.asyncio
    async def test_an_unfinished_run_returns_after_the_timeout_marked_timed_out(self, collaborators, orchestrator, clock):
        await start(orchestrator, "gen_1")

        body = payload(await call(collaborators, "wait_for_generation", {"generation_id": "gen_1", "timeout_seconds": 5}))

        assert (body["status"], body["finished"], body["timed_out"]) == ("running", False, True)
        assert clock.now == pytest.approx(5)

    @pytest.mark.asyncio
    async def test_the_wait_is_capped_at_the_maximum(self, collaborators, orchestrator, clock):
        await start(orchestrator, "gen_1")

        await call(collaborators, "wait_for_generation", {"generation_id": "gen_1", "timeout_seconds": 9999})

        assert clock.now == pytest.approx(loop_tools.WAIT_MAX_SECONDS)

    @pytest.mark.asyncio
    async def test_a_run_that_finishes_meanwhile_ends_the_wait_early(self, collaborators, orchestrator, clock, monkeypatch):
        await start(orchestrator, "gen_1")
        real_sleep = clock.sleep

        async def finishing_sleep(seconds):
            await real_sleep(seconds)
            if clock.now >= 3:
                await finish(orchestrator, "gen_1")

        monkeypatch.setattr(loop_tools.asyncio, "sleep", finishing_sleep)

        body = payload(await call(collaborators, "wait_for_generation", {"generation_id": "gen_1", "timeout_seconds": 30}))

        assert (body["status"], body["finished"], body["timed_out"]) == ("completed", True, False)
        assert clock.now == pytest.approx(3)

    @pytest.mark.asyncio
    async def test_an_already_finished_run_returns_at_once(self, collaborators, orchestrator, clock):
        await start(orchestrator, "gen_1")
        await finish(orchestrator, "gen_1")

        body = payload(await call(collaborators, "wait_for_generation", {"generation_id": "gen_1"}))

        assert body["finished"] is True
        assert clock.sleeps == []

    @pytest.mark.asyncio
    async def test_someone_elses_generation_cannot_be_waited_on(self, collaborators, orchestrator, clock):
        await start(orchestrator, "gen_1")

        result = await call(collaborators, "wait_for_generation", {"generation_id": "gen_1"}, user_id=OTHER)

        assert result["isError"] is True
        assert clock.sleeps == []

    @pytest.mark.asyncio
    async def test_a_non_numeric_timeout_is_refused(self, collaborators, orchestrator, clock):
        result = await call(collaborators, "wait_for_generation", {"generation_id": "gen_1", "timeout_seconds": "soon"})

        assert result["isError"] is True


class TestLoopGetGenerationResults:
    @pytest.fixture
    async def done(self, orchestrator, history):
        await start(orchestrator, "gen_1")
        await finish(orchestrator, "gen_1")
        return history

    @pytest.mark.asyncio
    async def test_images_come_back_as_image_content_with_seed_and_prompt(self, collaborators, done, store):
        done.details["gen_1"] = completed_detail([image_file("f1"), image_file("f2")])

        result = await call(collaborators, "get_generation_results", {"generation_id": "gen_1"})

        assert result["isError"] is False
        images = [block for block in result["content"] if block["type"] == "image"]
        assert [block["mimeType"] for block in images] == ["image/webp", "image/webp"]
        assert base64.b64decode(images[0]["data"]) == b"x" * 900_000
        body = payload(result)
        assert (body["seed"], body["prompt"], body["negative_prompt"]) == (111, "a red fox", "blur")
        assert body["seeds"] == [111, 222] and body["prompts"] == ["a red fox", "a blue fox"]
        assert [f["delivered_as"] for f in body["files"]] == ["image", "image"]
        assert body["details_url"] == "https://potion.test/history?generation=gen_1"

    @pytest.mark.asyncio
    async def test_the_text_part_always_comes_first(self, collaborators, done):
        done.details["gen_1"] = completed_detail([image_file("f1")])

        result = await call(collaborators, "get_generation_results", {"generation_id": "gen_1"})

        assert result["content"][0]["type"] == "text"

    @pytest.mark.asyncio
    async def test_a_video_is_a_resource_link_not_inline_bytes(self, collaborators, done, store):
        done.details["gen_1"] = completed_detail([video_file("v1")])

        result = await call(collaborators, "get_generation_results", {"generation_id": "gen_1"})

        links = [block for block in result["content"] if block["type"] == "resource_link"]
        assert links == [{
            "type": "resource_link", "uri": "https://potion.test/api/media/files/v1",
            "name": "v1.mp4", "mimeType": "video/mp4",
        }]
        assert store.fetched == []
        assert payload(result)["files"][0]["delivered_as"] == "link"

    @pytest.mark.asyncio
    async def test_images_beyond_the_limit_become_links(self, collaborators, done, store):
        done.details["gen_1"] = completed_detail([image_file(f"f{i}") for i in range(5)])

        result = await call(collaborators, "get_generation_results", {"generation_id": "gen_1", "max_images": 2})

        kinds = [block["type"] for block in result["content"][1:]]
        assert kinds == ["image", "image", "resource_link", "resource_link", "resource_link"]
        assert [file_id for file_id, _ in store.fetched] == ["f0", "f1"]

    @pytest.mark.asyncio
    async def test_the_image_count_is_capped_at_the_maximum(self, collaborators, done, store):
        done.details["gen_1"] = completed_detail([image_file(f"f{i}") for i in range(12)])
        store.sizes = {"large": 10, "medium": 10, "small": 10}

        result = await call(collaborators, "get_generation_results", {"generation_id": "gen_1", "max_images": 50})

        assert len([block for block in result["content"] if block["type"] == "image"]) == loop_tools.RESULT_IMAGES_MAX

    @pytest.mark.asyncio
    async def test_an_oversized_image_falls_back_to_a_smaller_size(self, collaborators, done, store):
        done.details["gen_1"] = completed_detail([image_file("f1")])
        store.sizes = {"large": 3_000_000, "medium": 2_000_000, "small": 100_000}

        result = await call(collaborators, "get_generation_results", {"generation_id": "gen_1"})

        assert store.fetched == [("f1", "large"), ("f1", "medium"), ("f1", "small")]
        image = next(block for block in result["content"] if block["type"] == "image")
        assert len(base64.b64decode(image["data"])) == 100_000

    @pytest.mark.asyncio
    async def test_an_image_too_big_at_every_size_is_linked_instead(self, collaborators, done, store):
        done.details["gen_1"] = completed_detail([image_file("f1")])
        store.sizes = {"large": 3_000_000, "medium": 3_000_000, "small": 3_000_000}

        result = await call(collaborators, "get_generation_results", {"generation_id": "gen_1"})

        assert [block["type"] for block in result["content"]] == ["text", "resource_link"]

    @pytest.mark.asyncio
    async def test_the_total_inline_bytes_are_bounded(self, collaborators, done, store):
        done.details["gen_1"] = completed_detail([image_file(f"f{i}") for i in range(8)])
        store.sizes = {"large": 1_400_000, "medium": 1_400_000, "small": 1_400_000}

        result = await call(collaborators, "get_generation_results", {"generation_id": "gen_1", "max_images": 8})

        inline = [block for block in result["content"] if block["type"] == "image"]
        assert sum(len(base64.b64decode(block["data"])) for block in inline) <= loop_tools.RESULT_TOTAL_BYTES
        assert len(inline) == 4

    @pytest.mark.asyncio
    async def test_the_medium_size_can_be_asked_for(self, collaborators, done, store):
        done.details["gen_1"] = completed_detail([image_file("f1")])

        await call(collaborators, "get_generation_results", {"generation_id": "gen_1", "image_size": "medium"})

        assert store.fetched == [("f1", "medium")]

    @pytest.mark.asyncio
    async def test_an_unknown_image_size_is_refused(self, collaborators, done):
        result = await call(collaborators, "get_generation_results", {"generation_id": "gen_1", "image_size": "huge"})

        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_only_final_files_are_returned_when_there_are_any(self, collaborators, done, store):
        done.details["gen_1"] = completed_detail([image_file("draft", final=False), image_file("final")])

        await call(collaborators, "get_generation_results", {"generation_id": "gen_1"})

        assert [file_id for file_id, _ in store.fetched] == ["final"]

    @pytest.mark.asyncio
    async def test_a_run_that_is_still_going_is_not_read(self, collaborators, orchestrator, history):
        await start(orchestrator, "gen_1")

        result = await call(collaborators, "get_generation_results", {"generation_id": "gen_1"})

        assert result["isError"] is True
        assert "wait_for_generation" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_a_failed_run_returns_its_error_not_files(self, collaborators, orchestrator, history, store):
        await start(orchestrator, "gen_1")
        orchestrator.status_tracker.get("gen_1").error_code = "cuda_oom"
        await finish(orchestrator, "gen_1", GenerationState.FAILED)
        history.details["gen_1"] = completed_detail([image_file("partial")])

        result = await call(collaborators, "get_generation_results", {"generation_id": "gen_1"})

        assert result["isError"] is True
        assert "No generation" not in result["content"][0]["text"]
        assert store.fetched == []

    @pytest.mark.asyncio
    async def test_another_users_results_are_not_returned(self, collaborators, done, store):
        done.details["gen_1"] = completed_detail([image_file("f1")])

        result = await call(collaborators, "get_generation_results", {"generation_id": "gen_1"}, user_id=OTHER)

        assert result["isError"] is True
        assert "No generation 'gen_1' of yours" in result["content"][0]["text"]
        assert store.fetched == []

    @pytest.mark.asyncio
    async def test_a_run_whose_history_hides_it_is_not_found(self, collaborators, done):
        result = await call(collaborators, "get_generation_results", {"generation_id": "gen_1"})

        assert result["isError"] is True
        assert "No generation 'gen_1' of yours" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_a_run_with_no_files_says_so(self, collaborators, done):
        done.details["gen_1"] = completed_detail([])

        result = await call(collaborators, "get_generation_results", {"generation_id": "gen_1"})

        assert result["isError"] is False
        assert payload(result)["message"] == "This generation produced no files."
        assert len(result["content"]) == 1

    @pytest.mark.asyncio
    async def test_the_seed_falls_back_to_the_submitted_one(self, collaborators, done):
        done.details["gen_1"] = completed_detail([image_file("f1")], parameters={})

        body = payload(await call(collaborators, "get_generation_results", {"generation_id": "gen_1"}))

        assert body["seed"] == 7

    @pytest.mark.asyncio
    async def test_existing_text_only_tools_keep_their_result_shape(self, collaborators):
        result = await call(collaborators, "get_generation_status", {"generation_id": "nope"})

        assert set(result) == {"content", "isError"}
        assert len(result["content"]) == 1


class TestLoopUploadMedia:
    @pytest.mark.asyncio
    async def test_an_image_is_stored_through_the_upload_path_as_the_callers_library_media(self, collaborators, store):
        result = await call(collaborators, "upload_media", {"filename": "ref.png", "data_base64": b64(PNG)})

        assert result["isError"] is False
        body = payload(result)
        assert body["path"] == "uploads/stored-ref.png"
        assert (body["media_type"], body["mime_type"], body["id"]) == ("image", "image/png", "up-1")
        store.upload_media.assert_awaited_once_with(
            file_data=PNG, filename="ref.png", content_type="image/png", user_id=OWNER, purpose="user_upload",
        )
        store.check_upload_size.assert_called_once_with(OWNER, len(PNG), "user_upload", "image/png")

    @pytest.mark.asyncio
    async def test_the_extension_follows_the_content_not_the_name(self, collaborators, store):
        await call(collaborators, "upload_media", {"filename": "photo.exe", "data_base64": b64(PNG)})

        assert store.upload_media.await_args.kwargs["filename"] == "photo.png"

    @pytest.mark.asyncio
    async def test_a_video_is_accepted(self, collaborators, store):
        result = await call(collaborators, "upload_media", {"filename": "clip.mp4", "data_base64": b64(MP4)})

        assert payload(result)["media_type"] == "video"

    @pytest.mark.asyncio
    async def test_a_file_over_the_size_limit_is_refused_before_anything_is_stored(self, collaborators, store):
        big = PNG + b"\x00" * loop_tools.UPLOAD_MAX_BYTES

        result = await call(collaborators, "upload_media", {"filename": "big.png", "data_base64": b64(big)})

        assert result["isError"] is True
        assert "larger than 10 MB" in result["content"][0]["text"]
        store.upload_media.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_file_just_inside_the_limit_is_accepted(self, collaborators, store):
        edge = PNG + b"\x00" * (loop_tools.UPLOAD_MAX_BYTES - len(PNG))

        result = await call(collaborators, "upload_media", {"filename": "edge.png", "data_base64": b64(edge)})

        assert result["isError"] is False

    @pytest.mark.asyncio
    async def test_a_payload_that_could_not_fit_is_refused_without_decoding(self, collaborators, store, monkeypatch):
        decode = Mock()
        monkeypatch.setattr(loop_tools, "_decode_upload", decode)
        result = await call(collaborators, "upload_media", {
            "filename": "x.png", "data_base64": "A" * (loop_tools.UPLOAD_MAX_BYTES * 2),
        })

        assert result["isError"] is True
        decode.assert_not_called()
        store.upload_media.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bytes_that_are_not_media_are_refused_whatever_the_name_says(self, collaborators, store):
        result = await call(collaborators, "upload_media", {"filename": "a.png", "data_base64": b64(b"MZ\x90\x00 exe")})

        assert result["isError"] is True
        assert "not a supported" in result["content"][0]["text"]
        store.upload_media.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_svg_is_not_accepted(self, collaborators, store):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

        result = await call(collaborators, "upload_media", {"filename": "a.svg", "data_base64": b64(svg)})

        assert result["isError"] is True
        store.upload_media.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_declared_type_that_contradicts_the_content_is_refused(self, collaborators, store):
        result = await call(collaborators, "upload_media", {
            "filename": "a.png", "data_base64": b64(MP4), "mime_type": "image/png",
        })

        assert result["isError"] is True
        assert "says image/png but the content is video/mp4" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_invalid_base64_is_refused(self, collaborators, store):
        result = await call(collaborators, "upload_media", {"filename": "a.png", "data_base64": "***not base64***"})

        assert result["isError"] is True
        assert "not valid base64" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_a_data_url_prefix_is_tolerated(self, collaborators, store):
        result = await call(collaborators, "upload_media", {
            "filename": "a.png", "data_base64": "data:image/png;base64," + b64(PNG),
        })

        assert result["isError"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("arguments", [{"data_base64": "AAAA"}, {"filename": "a.png"}, {"filename": " ", "data_base64": "AAAA"}])
    async def test_missing_arguments_are_refused(self, collaborators, store, arguments):
        result = await call(collaborators, "upload_media", arguments)

        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_a_refusal_from_the_upload_path_reaches_the_client(self, collaborators, store):
        store.upload_media.side_effect = ValueError("Upload blocked")

        result = await call(collaborators, "upload_media", {"filename": "a.png", "data_base64": b64(PNG)})

        assert (result["isError"], result["content"][0]["text"]) == (True, "Error: Upload blocked")

    @pytest.mark.asyncio
    async def test_a_plan_limit_refusal_reaches_the_client(self, collaborators, store):
        from src.features.plans.errors import LimitExceeded

        store.check_upload_size.side_effect = LimitExceeded(
            [{"message": "Storage is full", "kind": "storage", "code": "storage_full", "resets_at": None}], "upload", None,
        )

        result = await call(collaborators, "upload_media", {"filename": "a.png", "data_base64": b64(PNG)})

        assert result["content"][0]["text"] == "Error: Storage is full"
        store.upload_media.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_uploaded_path_is_the_one_the_library_listing_gives_out(self, collaborators, store):
        from src.features.llm.tools.builtin.library_items_tool import _upload_path

        body = payload(await call(collaborators, "upload_media", {"filename": "a.png", "data_base64": b64(PNG)}))

        assert body["path"] == _upload_path("stored-a.png")

    @pytest.mark.asyncio
    async def test_the_upload_is_unavailable_without_a_media_store(self, collaborators):
        bare = build_collaborators(collaborators.tool_registry, ToolServices(llm_repository=Mock(
            get_default_configuration=Mock(return_value=None),
        )))

        result = await call(bare, "upload_media", {"filename": "a.png", "data_base64": b64(PNG)})

        assert result["isError"] is True
