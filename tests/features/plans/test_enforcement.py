import os
import shutil
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.bootstrap.errors import register_error_handlers
from src.features.generation import GenerationHistoryFacade
from src.features.generation.dto import GenerationRequest
from src.features.generation.failure import start_failure_reason
from src.features.generation.history_archive import GenerationHistoryArchive
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.pipeline_builder import BuiltPipeline
from src.features.generation.routes import GenerationController
from src.features.generation.run_report_recorder import RunReportRecorder
from src.features.media.media_types import MediaTypeResolver
from src.features.media.records import Upload
from src.features.media.routes import MediaController
from src.features.media.store import MediaStore
from src.features.generation.file_repository import FileRepository
from src.features.media.upload_repository import UploadRepository
from src.features.media_tools.repository import MediaToolParamsRepository
from src.features.media_tools.service import MediaToolService
from src.features.plans.errors import LimitExceeded
from src.features.user_groups.constants import ALL_USERS_GROUP_ID
from src.platform.filesystem import FileStore
from src.platform.filesystem.storage_driver import LocalFileStorageDriver
from src.platform.media_tools import MediaTool, MediaToolRegistry, ToolSpec
from tests.features.plans.conftest import make_plan


def limit_storage(plans, value):
    plan = make_plan(plans, "Tight", storage_bytes=value)
    plans.guard.plans.set_group_plan(ALL_USERS_GROUP_ID, plan.id)


def limit_daily(plans, value):
    plan = make_plan(plans, "Daily", generations_per_day=value)
    plans.guard.plans.set_group_plan(ALL_USERS_GROUP_ID, plan.id)


@pytest.fixture(autouse=True)
def bind_form_passthrough():
    from src.features.forms.binding import BoundForm

    def passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or "custom", coercions=[], stripped=[])

    with patch("src.features.generation.orchestrator.bind_form", side_effect=passthrough):
        yield


@pytest.fixture
def repo():
    with patch("src.features.generation.orchestrator.generation_repo") as mock_repo, \
         patch("src.features.generation.status_tracker.generation_repo", mock_repo):
        mock_repo.get_by_id = Mock(return_value=Mock(user_id="u1"))
        mock_repo.get_by_idempotency_key = Mock(return_value=None)
        yield mock_repo


@pytest.fixture
def backend():
    backend = Mock()
    backend.backend_id = "b1"
    backend.name = "Local"
    backend.engine = "native"
    backend.start_generation = AsyncMock()
    return backend


def orchestrator_for(backend, guard):
    registry = Mock()
    registry.select_backend_for_generation = Mock(return_value=backend)
    registry.get_backend = Mock(return_value=backend)
    builder = Mock()
    builder.build_pipeline = Mock(return_value=BuiltPipeline(
        generation_id="g", preset_id="p", preset_template=Mock(version="1"), pipes=[],
    ))
    loader = Mock()
    loader.load_preset_by_id = Mock(return_value=Mock(engine="native"))
    settings = Mock()
    settings.get_setting = Mock(return_value="/out")
    return GenerationOrchestrator(
        pipeline_builder=builder, backend_registry=registry, connection_hub=Mock(), settings=settings,
        output_processor=Mock(), preset_template_loader=loader, limit_guard=guard,
    )


def make_request(key=None):
    request = Mock()
    request.preset_id = "p"
    request.form_data = {"quantity": 4, "seed": 5}
    request.prompts = None
    request.variables = None
    request.prompt_state = None
    request.mode = "txt2img"
    request.tag_ids = None
    request.source_prompt_id = None
    request.segments = None
    request.collection_ids = None
    request.idempotency_key = key
    return request


def ledger(seed):
    return seed.rows("SELECT ref_id, refunded_at FROM limit_events ORDER BY created_at")


@pytest.mark.asyncio
async def test_submit_is_refused_before_anything_is_persisted(seed, plans, repo, backend):
    seed.user("u1")
    limit_daily(plans, 0)
    orchestrator = orchestrator_for(backend, plans.guard)

    with pytest.raises(LimitExceeded) as refused:
        await orchestrator.start_generation(make_request(), "u1")

    assert refused.value.payload()["code"] == "daily_generations_exceeded"
    repo.create.assert_not_called()
    backend.start_generation.assert_not_awaited()
    assert ledger(seed) == []


@pytest.mark.asyncio
async def test_submit_admission_runs_off_the_event_loop_thread(seed, plans, repo, backend):
    seed.user("u1")
    threads = []
    real = plans.guard.admit

    def spy(request):
        threads.append(threading.current_thread())
        return real(request)

    plans.guard.admit = spy
    orchestrator = orchestrator_for(backend, plans.guard)

    await orchestrator.start_generation(make_request(), "u1")

    assert threads
    assert all(thread is not threading.main_thread() for thread in threads)


@pytest.mark.asyncio
async def test_an_accepted_batch_counts_once_against_its_generation(seed, plans, repo, backend):
    seed.user("u1")
    limit_daily(plans, 5)
    orchestrator = orchestrator_for(backend, plans.guard)

    with patch("src.features.generation.orchestrator.generate_ulid", return_value="g1"):
        await orchestrator.start_generation(make_request(), "u1")

    assert ledger(seed) == [{"ref_id": "g1", "refunded_at": None}]
    repo.create.assert_called_once()


@pytest.mark.asyncio
async def test_an_idempotent_retry_is_not_counted_again(seed, plans, repo, backend):
    seed.user("u1")
    limit_daily(plans, 5)
    orchestrator = orchestrator_for(backend, plans.guard)
    existing = Mock(id="g1", idempotency_key="k", idempotency_fingerprint=None, status="completed",
                    user_id="u1", preset_id="p", progress=1.0, error_message=None, created_at=None,
                    completed_at=None)
    repo.get_by_idempotency_key = Mock(return_value=existing)
    repo.get_by_id = Mock(return_value=existing)

    result = await orchestrator.start_generation(make_request(key="k"), "u1")

    assert result["generation_id"] == "g1"
    assert ledger(seed) == []


@pytest.mark.asyncio
async def test_a_submit_that_fails_after_admission_is_refunded(seed, plans, repo, backend):
    seed.user("u1")
    limit_daily(plans, 1)
    orchestrator = orchestrator_for(backend, plans.guard)
    repo.create = Mock(side_effect=RuntimeError("disk full"))

    with pytest.raises(RuntimeError):
        await orchestrator.start_generation(make_request(), "u1")

    assert [row["refunded_at"] is not None for row in ledger(seed)] == [True]
    repo.create = Mock()
    await orchestrator.start_generation(make_request(), "u1")


@pytest.mark.asyncio
async def test_the_generation_route_answers_403_with_the_refusal():
    error = LimitExceeded([{
        "kind": "generations_per_day", "code": "daily_generations_exceeded", "label": "Generations per day",
        "format": "count", "used": 2, "limit": 2, "percent": 100.0, "resets_at": "x", "message": "Daily limit reached.",
    }], "submit", "Ask your admin for more.")
    orchestrator = Mock(spec=GenerationOrchestrator)
    orchestrator.status_tracker = Mock()
    orchestrator.start_generation = AsyncMock(side_effect=error)
    controller = GenerationController(
        orchestrator, Mock(spec=GenerationHistoryFacade), Mock(spec=FileStore), Mock(spec=RunReportRecorder),
    )

    with pytest.raises(LimitExceeded) as raised:
        await controller.start_generation(GenerationRequest(preset_id="p1", prompt="x", form_data={}), Mock(id="u1"))

    assert raised.value.status_code == 403
    assert raised.value.payload()["error"] == "limit_exceeded"
    assert raised.value.payload()["kinds"][0]["code"] == "daily_generations_exceeded"


def media_store(tmp_path, guard):
    driver = Mock()
    driver.put_bytes = Mock()
    upload_repo = Mock()
    upload_repo.find_by_hash = Mock(return_value=None)
    upload_repo.create = Mock(return_value=Upload(id="up_1", user_id="u1", filename="stored.bin", media_type="image"))
    plugins = Mock()
    plugins.execute_hook = Mock(side_effect=lambda hook, initial_data: (SimpleNamespace(data=initial_data), True))
    store = MediaStore(
        file_resolver=Mock(), image_processor=Mock(), media_type_resolver=MediaTypeResolver(),
        file_repository=Mock(), generation_repository=Mock(), settings=Mock(), file_service=Mock(),
        plugin_registry=plugins, upload_repository=upload_repo, storage_driver=driver, limit_guard=guard,
    )
    return store, driver


@pytest.mark.asyncio
async def test_an_upload_that_does_not_fit_is_refused_before_any_byte_is_written(seed, plans, tmp_path):
    seed.user("u1")
    limit_storage(plans, 10)
    seed.upload("u1", 8)
    store, driver = media_store(tmp_path, plans.guard)

    with pytest.raises(LimitExceeded) as refused:
        await store.upload_media(b"abc", "a.png", "image/png", user_id="u1")

    assert refused.value.payload()["point"] == "upload"
    driver.put_bytes.assert_not_called()


def record_threads(guard, monkeypatch, *names):
    threads = []
    for name in names:
        real = getattr(guard, name)

        def spy(*args, _real=real, **kwargs):
            threads.append(threading.current_thread())
            return _real(*args, **kwargs)

        monkeypatch.setattr(guard, name, spy)
    return threads


@pytest.mark.asyncio
async def test_upload_admission_runs_off_the_event_loop_thread(seed, plans, tmp_path, monkeypatch):
    seed.user("u1")
    threads = record_threads(plans.guard, monkeypatch, "check_items", "admit")
    store, driver = media_store(tmp_path, plans.guard)

    await store.upload_media(b"abc", "a.png", "image/png", user_id="u1")

    assert len(threads) == 2
    assert all(thread is not threading.main_thread() for thread in threads)


def test_the_upload_route_answers_403(plans):
    error = LimitExceeded([{
        "kind": "storage_bytes", "code": "storage_quota_exceeded", "label": "Storage space", "format": "bytes",
        "used": 10, "limit": 10, "percent": 100.0, "resets_at": None, "message": "Your storage is full.",
    }], "upload", "")
    store = Mock()
    store.upload_media = AsyncMock(side_effect=error)
    controller = MediaController(store, Mock())
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/upload")
    async def route():
        return await controller.upload_media(Mock(read=AsyncMock(return_value=b"x"), filename="a.png",
                                                  content_type="image/png"), Mock(id="u1"))

    response = TestClient(app).post("/upload")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "storage_quota_exceeded"


def test_an_unhandled_refusal_still_answers_403_through_the_app_handler():
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise LimitExceeded([{
            "kind": "storage_bytes", "code": "storage_quota_exceeded", "label": "Storage", "format": "bytes",
            "used": 1, "limit": 1, "percent": 100.0, "resets_at": None, "message": "Full.",
        }], "upload", "")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "limit_exceeded"


class Stamp(MediaTool):
    def __init__(self, result):
        self.result = result

    def run(self, inputs, params, ctx):
        out = ctx.new_path(".png", stem="stamp")
        shutil.copyfile(self.result, out)
        return [ctx.probe(out, "image")]


@pytest.fixture
def tools(seed, plans, tmp_path):
    seed.user("u1")
    driver = LocalFileStorageDriver(str(tmp_path / "store"))
    uploads = UploadRepository()
    source = tmp_path / "scratch.png"
    Image.frombytes("RGB", (32, 32), os.urandom(32 * 32 * 3)).save(source)
    size = driver.put_file("uploads/source.png", source)
    upload = uploads.create(Upload(user_id="u1", filename="source.png", original_filename="source.png",
                                   media_type="image", mime_type="image/png", width=32, height=32, file_size=size))
    big = tmp_path / "big.png"
    Image.frombytes("RGB", (96, 96), os.urandom(96 * 96 * 3)).save(big)
    small = tmp_path / "small.png"
    Image.new("RGB", (4, 4), (1, 1, 1)).save(small)
    registry = MediaToolRegistry()
    registry.register(ToolSpec(id="stamp", label="Stamp", accepts=("image",), produces="same", impl=Stamp(big)))
    service = MediaToolService(
        registry, MediaToolParamsRepository(), uploads, FileRepository(), MediaTypeResolver(), driver,
        limit_guard=plans.guard, scratch_root=tmp_path / "tool-scratch", sweep_stale=False,
    )
    sizes = SimpleNamespace(source=size, big=big.stat().st_size, small=small.stat().st_size)
    assert sizes.small < sizes.source < sizes.big
    yield service, upload, driver, sizes, {"big": big, "small": small}
    service.shutdown(2)


def stored_files(driver):
    return sorted(p.relative_to(driver.base_dir).as_posix() for p in driver.base_dir.rglob("*") if p.is_file())


def upload_rows(seed):
    return seed.rows("SELECT filename FROM uploads WHERE user_id = 'u1' ORDER BY filename")


def stamp(service, upload, mode="new"):
    return service.run_blocking(
        "u1", [{"tool": "stamp", "params": {}}], [{"item_id": upload.id}], {"mode": mode}, timeout=20,
    )


def stamp_with(service, path):
    service.registry.get("stamp").impl.result = path


@pytest.mark.asyncio
async def test_media_tool_admission_runs_off_the_event_loop_thread(tools, plans, monkeypatch):
    service, upload, driver, sizes, results = tools
    threads = record_threads(plans.guard, monkeypatch, "check", "admit")

    result = await service.run_sync(
        "u1", [{"tool": "stamp", "params": {}}], [{"item_id": upload.id}], {"mode": "new"}, timeout=20,
    )

    assert result.ok and len(threads) == 2
    assert all(thread is not threading.main_thread() for thread in threads)


def test_a_result_that_does_not_fit_is_refused_and_leaves_no_file_or_row(tools, plans, seed):
    service, upload, driver, sizes, results = tools
    limit_storage(plans, sizes.source + sizes.small)

    refused = stamp(service, upload)

    assert (refused.status, refused.error.code) == ("failed", "limit_exceeded")
    assert upload_rows(seed) == [{"filename": "source.png"}]
    assert stored_files(driver) == ["uploads/source.png"]

    stamp_with(service, results["small"])
    assert stamp(service, upload).ok is True
    assert len(upload_rows(seed)) == 2


def test_a_full_account_is_refused_before_the_tool_runs(tools, plans, seed):
    service, upload, driver, sizes, results = tools
    limit_storage(plans, sizes.source)
    ran = Mock(side_effect=AssertionError("ran while full"))
    service.registry.get("stamp").impl.run = ran

    with pytest.raises(LimitExceeded) as refused:
        service.start_run("u1", [{"tool": "stamp", "params": {}}], [{"item_id": upload.id}], {"mode": "new"})

    assert refused.value.status_code == 403
    assert refused.value.payload()["code"] == "storage_quota_exceeded"
    ran.assert_not_called()
    assert stored_files(driver) == ["uploads/source.png"]


def test_a_replace_that_grows_past_the_limit_is_refused_and_keeps_the_original(tools, plans, seed):
    service, upload, driver, sizes, results = tools
    limit_storage(plans, sizes.source + 10)

    refused = stamp(service, upload, "replace")

    assert (refused.status, refused.error.code) == ("failed", "limit_exceeded")
    assert upload_rows(seed) == [{"filename": "source.png"}]
    assert stored_files(driver) == ["uploads/source.png"]

    stamp_with(service, results["small"])
    accepted = stamp(service, upload, "replace")
    assert accepted.ok is True and accepted.replaced is True


def test_a_full_account_can_still_replace_with_a_smaller_result(tools, plans, seed):
    service, upload, driver, sizes, results = tools
    stamp_with(service, results["small"])
    limit_storage(plans, sizes.source)

    result = stamp(service, upload, "replace")

    assert result.ok is True and result.replaced is True


def test_library_copy_and_inspiration_save_are_refused_when_full(seed, plans, tmp_path):
    from src.features.inspirations.operations.saves import save_to_library
    from src.features.library.operations.mutations import copy_generation_file

    seed.user("u1")
    limit_storage(plans, 10)
    seed.upload("u1", 10)
    source = tmp_path / "out.png"
    source.write_bytes(b"12345")
    driver = Mock()
    library = SimpleNamespace(
        file_repository=Mock(get_by_id=Mock(return_value=Mock(file_type="IMAGE", file_path="out.png", file_size=5))),
        file_store=Mock(get_full_path=Mock(return_value=str(source)), base_storage_dir=str(tmp_path)),
        file_resolver=Mock(validate_path_security=Mock(return_value=True)),
        storage_driver=driver,
        limit_guard=plans.guard,
    )
    inspirations = SimpleNamespace(
        repository=Mock(get_by_id=Mock(return_value=Mock(media=[{"filename": "out.png", "file_size": 5}]))),
        file_store=library.file_store,
        file_resolver=library.file_resolver,
        storage_driver=driver,
        limit_guard=plans.guard,
    )

    with pytest.raises(LimitExceeded):
        copy_generation_file(library, "f1", "u1")
    with pytest.raises(LimitExceeded):
        save_to_library(inspirations, "i1", "u1")

    driver.put_file.assert_not_called()


STORAGE_REFUSAL = {
    "kind": "storage_bytes", "code": "storage_quota_exceeded", "label": "Storage space", "format": "bytes",
    "used": 10, "limit": 10, "percent": 100.0, "resets_at": None, "message": "Your storage is full.",
}


def history_archive(guard):
    generation_repo = Mock()
    generation_repo.add_file = Mock(side_effect=lambda generation_id, record: Mock(to_dict=Mock(return_value={})))
    file_service = Mock()
    plugins = Mock()
    plugins.execute_hook = Mock(side_effect=lambda hook, initial_data: (SimpleNamespace(data=initial_data), True))
    archive = GenerationHistoryArchive(generation_repo, file_service, plugins, Mock(), Mock(), limit_guard=guard)
    return archive, generation_repo, file_service.storage_driver


def history_file(data, content_type="audio/mpeg", size=None):
    upload = Mock()
    upload.filename = "take.mp3"
    upload.content_type = content_type
    upload.size = size
    upload.read = AsyncMock(return_value=data)
    upload.seek = AsyncMock()
    return upload


@pytest.mark.asyncio
async def test_uploading_files_into_history_that_do_not_fit_is_refused_before_anything_is_written(seed, plans):
    seed.user("u1")
    limit_storage(plans, 10)
    seed.upload("u1", 4)
    archive, generation_repo, driver = history_archive(plans.guard)
    files = [history_file(b"abc"), history_file(b"abcd"), history_file(b"x" * 99, content_type="text/plain")]

    with pytest.raises(LimitExceeded) as refused:
        await archive.upload_generations(files, [], "u1")

    assert refused.value.payload()["point"] == "upload"
    generation_repo.create.assert_not_called()
    driver.put_bytes.assert_not_called()

    files = [history_file(b"abc"), history_file(b"", size=3), history_file(b"x" * 99, content_type="text/plain")]
    result = await archive.upload_generations(files, [], "u1")
    assert len(result["files"]) == 2


@pytest.mark.asyncio
async def test_the_history_upload_route_answers_403_with_the_refusal():
    facade = Mock(spec=GenerationHistoryFacade)
    facade.upload_generations = AsyncMock(side_effect=LimitExceeded([STORAGE_REFUSAL], "upload", ""))
    orchestrator = Mock(spec=GenerationOrchestrator)
    orchestrator.status_tracker = Mock()
    controller = GenerationController(orchestrator, facade, Mock(spec=FileStore), Mock(spec=RunReportRecorder))

    with pytest.raises(LimitExceeded) as raised:
        await controller.upload_generations([Mock()], [], Mock(id="u1"))

    assert raised.value.status_code == 403
    assert raised.value.payload()["code"] == "storage_quota_exceeded"


def cloud_orchestrator(backend, guard, engine):
    backend.engine = engine
    orchestrator = orchestrator_for(backend, guard)
    orchestrator.preset_template_loader.load_preset_by_id = Mock(return_value=Mock(engine=engine))
    return orchestrator


@pytest.mark.asyncio
async def test_a_cloud_submit_over_the_monthly_budget_is_refused_and_a_local_one_is_not(seed, plans, repo, backend):
    seed.user("u1")
    plan = make_plan(plans, "Budget", cloud_spend_usd_month=10)
    plans.guard.plans.set_group_plan(ALL_USERS_GROUP_ID, plan.id)
    seed.cost("u1", 10, plans.guard.clock())

    with pytest.raises(LimitExceeded) as refused:
        await cloud_orchestrator(backend, plans.guard, "cloud").start_generation(make_request(), "u1")

    assert refused.value.payload()["code"] == "cloud_budget_exceeded"
    assert refused.value.payload()["used"] is None
    repo.create.assert_not_called()

    await cloud_orchestrator(backend, plans.guard, "native").start_generation(make_request(), "u1")
    repo.create.assert_called_once()


def test_chat_and_mcp_callers_get_the_plain_refusal_not_a_generic_failure():
    error = LimitExceeded([STORAGE_REFUSAL], "submit", "")

    assert start_failure_reason(error, privileged=False) == "Your storage is full."
