import asyncio
import json
import re
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.error_classification import (
    ERROR_CATEGORIES,
    classification_for_code,
    user_classification_for_code,
)
from src.features.generation.failure import (
    GenerationFailure,
    apply_failure,
    failure_from_output,
    format_hint,
    scope_generation,
    scope_run_report,
    scope_status,
)
from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.notifier import GenerationNotifier
from src.features.generation.output_broadcaster import GenerationOutputBroadcaster
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository
from src.features.generation.routes import GenerationController, build_router
from src.features.generation.status_tracker import GenerationState, GenerationStatusTracker
from src.pipelines.outputs import ErrorGenerationOutput
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from src.platform.util.ids import generate_ulid
from src.platform.websocket.connection_hub import ConnectionHub

VRAM_NOTE = "(0.45GB free of 31.84GB total VRAM)"
RAW_ERROR = "RuntimeError: failed in /srv/models/private/unet.safetensors"
TRACEBACK = 'Traceback (most recent call last):\n  File "/srv/app/src/pipelines/pipes/loader.py", line 42\n'
TECHNICAL_MARKERS = ("VRAM", "GB", "fp8", "/srv/", "Traceback", "->", "Administration", "GPU process", "applications using")
VIDEO_FORM = {"prompt": "a fox", "video_director": {"settings": {"duration": 5, "fps": 24}}}
IMAGE_FORM = {"prompt": "a fox"}


def _admin_message(code):
    summary = classification_for_code(code).summary
    return f"{summary} {VRAM_NOTE}" if code == "cuda_oom" else summary


def _admin_hints(code):
    return tuple(classification_for_code(code).suggestions)


def _failure(code):
    return GenerationFailure(
        error_code=code,
        message=_admin_message(code),
        hints=_admin_hints(code),
        raw_error=RAW_ERROR,
        detail=TRACEBACK,
        failed_pipe_id="loader",
        failed_pipe_name="model_loader",
        failed_at_step="Loading 1/2",
    )


def _error_output(code):
    output = ErrorGenerationOutput(
        error=RAW_ERROR,
        detail=TRACEBACK,
        error_code=code,
        message=_admin_message(code),
        hints=list(_admin_hints(code)),
        pipe_key="loader",
        failed_at_step="Loading 1/2",
    )
    output.pipe_id = 1
    output.pipe_name = "model_loader"
    return apply_failure(output, failure_from_output(output))


def _assert_plain(message, hint):
    rendered = f"{message}\n{hint}"
    assert not re.search(r"\d", rendered), rendered
    for marker in TECHNICAL_MARKERS:
        assert marker not in rendered, marker


def _user(account_type, user_id):
    return User(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.com",
        password_hash="$2b$12$x",
        account_type=account_type,
        created_at=datetime.utcnow(),
        last_login=None,
    )


class _Socket:
    def __init__(self):
        self.sent = []

    async def accept(self):
        return None

    async def send_text(self, text):
        self.sent.append(text)


class TestUserCopy:
    @pytest.mark.parametrize("code", ERROR_CATEGORIES)
    def test_every_category_has_plain_user_copy_with_at_most_two_actions(self, code):
        copy = user_classification_for_code(code)

        assert copy.category == code
        assert copy.summary
        assert 1 <= len(copy.suggestions) <= 2
        _assert_plain(copy.summary, "\n".join(copy.suggestions))

    @pytest.mark.parametrize("code", ERROR_CATEGORIES)
    def test_the_video_variant_stays_plain(self, code):
        copy = user_classification_for_code(code, video=True)

        assert 1 <= len(copy.suggestions) <= 2
        _assert_plain(copy.summary, "\n".join(copy.suggestions))

    def test_an_unknown_code_falls_back_to_the_generic_copy(self):
        copy = user_classification_for_code("something_new")

        assert copy.category == "unclassified"
        assert copy.summary == "Something went wrong while generating."
        assert copy.suggestions == ["Try again", "If it keeps happening, send the error ID to your administrator"]

    def test_out_of_memory_offers_fewer_frames_only_for_video(self):
        assert user_classification_for_code("cuda_oom").suggestions == ["Try a lower resolution", "Try again in a moment"]
        assert user_classification_for_code("cuda_oom", video=True).suggestions == ["Try a lower resolution", "Try fewer frames"]
        assert user_classification_for_code("cuda_oom").summary == "This was too much for the server's GPU right now."


class TestWebSocketPath:
    async def _subscribe(self, hub, client_id, privileged):
        socket = _Socket()
        await hub.connect(socket, client_id)
        await hub.subscribe_to_generation(client_id, "gen-1", privileged=privileged)
        return socket

    def _broadcaster(self, tracker=None):
        return GenerationOutputBroadcaster(ConnectionHub(), tracker or Mock(), Mock())

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", ERROR_CATEGORIES)
    async def test_generation_error_is_plain_for_users_and_full_for_admins(self, code):
        broadcaster = self._broadcaster()
        user_socket = await self._subscribe(broadcaster.connection_hub, "u", privileged=False)
        admin_socket = await self._subscribe(broadcaster.connection_hub, "a", privileged=True)

        with patch("src.features.generation.output_broadcaster.generation_repo") as repo:
            repo.get_by_id.return_value = SimpleNamespace(form_data=IMAGE_FORM, mode="txt2img")
            await broadcaster.broadcast_output("gen-1", _error_output(code), SimpleNamespace(preset_id="p1"))

        user_message = json.loads(user_socket.sent[-1])
        admin_message = json.loads(admin_socket.sent[-1])
        expected = user_classification_for_code(code)

        assert user_message["error_code"] == code
        assert user_message["error_id"] == "gen-1"
        assert user_message["message"] == expected.summary
        assert user_message["hint"] == format_hint(expected.suggestions)
        assert "detail" not in user_message
        _assert_plain(user_message["message"], user_message["hint"])

        assert admin_message["error_code"] == code
        assert admin_message["error_id"] == "gen-1"
        assert admin_message["message"] == _admin_message(code)
        assert admin_message["hint"] == format_hint(_admin_hints(code))
        assert TRACEBACK in admin_message["detail"]

    @pytest.mark.asyncio
    async def test_the_vram_note_reaches_only_admins(self):
        broadcaster = self._broadcaster()
        user_socket = await self._subscribe(broadcaster.connection_hub, "u", privileged=False)
        admin_socket = await self._subscribe(broadcaster.connection_hub, "a", privileged=True)

        with patch("src.features.generation.output_broadcaster.generation_repo") as repo:
            repo.get_by_id.return_value = SimpleNamespace(form_data=VIDEO_FORM, mode="video")
            await broadcaster.broadcast_output("gen-1", _error_output("cuda_oom"), SimpleNamespace(preset_id="p1"))

        assert VRAM_NOTE not in user_socket.sent[-1]
        assert "Switch to an fp8" not in user_socket.sent[-1]
        assert "Try fewer frames" in json.loads(user_socket.sent[-1])["hint"]
        assert VRAM_NOTE in json.loads(admin_socket.sent[-1])["message"]
        assert "Switch to an fp8 or smaller model variant" in json.loads(admin_socket.sent[-1])["hint"]

    @pytest.mark.asyncio
    async def test_the_run_report_keeps_the_classified_message(self):
        broadcaster = self._broadcaster()
        await self._subscribe(broadcaster.connection_hub, "u", privileged=False)

        with patch("src.features.generation.output_broadcaster.generation_repo") as repo:
            repo.get_by_id.return_value = None
            await broadcaster.broadcast_output("gen-1", _error_output("cuda_oom"), SimpleNamespace(preset_id="p1"))

        recorded = broadcaster.run_report_recorder.record_output.call_args[0][1]
        assert recorded["message"] == _admin_message("cuda_oom")
        assert "detail" not in recorded

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", ERROR_CATEGORIES)
    async def test_the_terminal_status_is_plain_for_users_and_full_for_admins(self, code):
        tracker = GenerationStatusTracker()
        tracker.create(id="gen-1", user_id="u1")
        with patch("src.features.generation.status_tracker.generation_repo"):
            tracker.transition("gen-1", GenerationState.FAILED, _failure(code))
        broadcaster = self._broadcaster(tracker)
        user_socket = await self._subscribe(broadcaster.connection_hub, "u", privileged=False)
        admin_socket = await self._subscribe(broadcaster.connection_hub, "a", privileged=True)

        await broadcaster.handle_output("gen-1", None)

        user_message = json.loads(user_socket.sent[-1])
        admin_message = json.loads(admin_socket.sent[-1])
        assert user_message["type"] == "generation_complete"
        assert user_message["data"]["message"] == user_classification_for_code(code).summary
        assert user_message["data"]["error_id"] == "gen-1"
        _assert_plain(user_message["data"]["message"], "")
        assert admin_message["data"]["message"] == _admin_message(code)
        assert admin_message["data"]["error_id"] == "gen-1"


class TestNotificationPath:
    @pytest.mark.parametrize("code", ERROR_CATEGORIES)
    def test_regular_owners_get_plain_copy_and_admins_the_full_message(self, code):
        manager = Mock()
        with patch("src.platform.plugins.runtime_registries.get_global_notification_manager", return_value=manager):
            GenerationNotifier().notify_failure("gen-1", "u1", _failure(code))
            GenerationNotifier().notify_failure("gen-1", "a1", _failure(code), include_detail=True)

        user_kwargs, admin_kwargs = (call.kwargs for call in manager.call_args_list)
        expected = user_classification_for_code(code)
        assert user_kwargs["message"] == expected.summary
        assert user_kwargs["metadata"]["hint"] == format_hint(expected.suggestions)
        assert user_kwargs["metadata"]["error_id"] == "gen-1"
        assert user_kwargs["metadata"]["contact_admin"] is True
        assert admin_kwargs["metadata"]["contact_admin"] is False
        _assert_plain(user_kwargs["message"], user_kwargs["metadata"]["hint"])
        assert admin_kwargs["message"] == _admin_message(code)
        assert admin_kwargs["metadata"]["hint"] == format_hint(_admin_hints(code))
        assert TRACEBACK in admin_kwargs["metadata"]["detail"]


class TestScopeHelpers:
    def test_a_status_that_did_not_fail_is_left_alone(self):
        status = {"status": "running", "message": "Loading 1/2", "error_code": None}

        assert scope_status(status, False) == status

    def test_an_admin_row_keeps_its_message_and_is_told_not_to_contact_an_admin(self):
        data = {"id": "g", "status": "failed", "error_code": "cuda_oom", "error_message": _admin_message("cuda_oom")}

        assert scope_generation(data, True) == {**data, "error_contact_admin": False}

    def test_a_generation_without_an_error_is_left_alone(self):
        data = {"id": "g", "status": "completed", "error_message": None, "error_code": None, "error_user_message": None}

        assert scope_generation(data, False) == data

    def test_the_run_report_failure_text_is_plain_for_users_only(self):
        report = {
            "status_history": [
                {"step": "Loading 1/2", "message": "Loading"},
                {"step": "failed", "message": _admin_message("cuda_oom")},
            ],
            "plugin_outputs": {
                "generation_error": {
                    "plugin_id": "model_loader",
                    "message": {"type": "generation_error", "error_code": "cuda_oom", "message": _admin_message("cuda_oom"), "hint": format_hint(_admin_hints("cuda_oom"))},
                },
            },
        }

        scoped = scope_run_report(report, False, "cuda_oom", video=True)

        assert scoped["status_history"][0] == report["status_history"][0]
        assert scoped["status_history"][1]["message"] == "This was too much for the server's GPU right now."
        error = scoped["plugin_outputs"]["generation_error"]["message"]
        assert error["message"] == "This was too much for the server's GPU right now."
        assert error["hint"] == "- Try a lower resolution\n- Try fewer frames"
        assert VRAM_NOTE not in json.dumps(scoped)
        assert scope_run_report(report, True, "cuda_oom") is report


class TestHistoryPath(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.generation_repo = GenerationRepository()
        self.history_facade = GenerationHistoryFacade(
            generation_repo=self.generation_repo,
            file_service=Mock(),
            plugin_registry=Mock(),
            run_report_repository=Mock(),
        )
        self.orchestrator = Mock()
        self.orchestrator.get_generation_status = AsyncMock(return_value=None)
        run_report_recorder = Mock()
        run_report_recorder.has_reports.return_value = set()
        self.run_report_recorder = run_report_recorder
        self.controller = GenerationController(self.orchestrator, self.history_facade, Mock(), run_report_recorder)
        self.owner_id = self.create_test_user("owner-1", "owner", "owner@example.com")
        self.admin_id = self.create_test_user("admin-1", "admin", "admin@example.com")
        self.owner = _user(AccountType.USER, self.owner_id)
        self.admin = _user(AccountType.ADMIN, self.admin_id)

    def _failed(self, user_id, code, form_data=IMAGE_FORM, mode="txt2img"):
        generation = self.generation_repo.create(Generation(
            id=generate_ulid(), preset_id="preset-1", form_data=dict(form_data), user_id=user_id, status="failed", mode=mode,
        ))
        self.generation_repo.update_status(generation.id, "failed", failure=_failure(code).columns())
        return generation.id

    def _client(self, user):
        app = FastAPI()
        app.include_router(build_router(SimpleNamespace(_generation_controller=self.controller)))
        app.dependency_overrides[get_current_active_user] = lambda: user
        return TestClient(app)

    def _history_row(self, user, generation_id):
        response = self._client(user).get("/api/generations/history")
        self.assertEqual(response.status_code, 200)
        return next(r for r in response.json()["data"]["generations"] if r["id"] == generation_id)

    def test_every_category_reads_plain_for_users_and_full_for_admins(self):
        for code in ERROR_CATEGORIES:
            with self.subTest(code=code):
                user_gen = self._failed(self.owner_id, code)
                admin_gen = self._failed(self.admin_id, code)
                expected = user_classification_for_code(code)
                full_user_message = f"{_admin_message(code)}\n\n{format_hint(_admin_hints(code))}"

                for row in (
                    self._history_row(self.owner, user_gen),
                    asyncio.run(self.controller.get_generation_by_id(user_gen, self.owner)).data,
                    asyncio.run(self.controller.get_generation_status(user_gen, self.owner)).data,
                ):
                    self.assertEqual(row["error_code"], code)
                    self.assertEqual(row["error_id"], user_gen)
                    self.assertEqual(row["error_message"], expected.summary)
                    self.assertEqual(row["error_user_message"], f"{expected.summary}\n\n{format_hint(expected.suggestions)}")
                    self.assertIs(row["error_contact_admin"], True)
                    _assert_plain(row["error_message"], row["error_user_message"])

                for row in (
                    self._history_row(self.admin, admin_gen),
                    asyncio.run(self.controller.get_generation_by_id(admin_gen, self.admin)).data,
                    asyncio.run(self.controller.get_generation_status(admin_gen, self.admin)).data,
                ):
                    self.assertEqual(row["error_code"], code)
                    self.assertEqual(row["error_id"], admin_gen)
                    self.assertEqual(row["error_message"], _admin_message(code))
                    self.assertEqual(row["error_user_message"], full_user_message)
                    self.assertIs(row["error_contact_admin"], False)

    def test_a_video_generation_offers_fewer_frames_to_users(self):
        generation_id = self._failed(self.owner_id, "cuda_oom", form_data=VIDEO_FORM, mode="video")

        row = self._history_row(self.owner, generation_id)

        self.assertIn("Try fewer frames", row["error_user_message"])
        self.assertNotIn(VRAM_NOTE, row["error_user_message"])

    def test_an_image_generation_does_not_mention_frames(self):
        generation_id = self._failed(self.owner_id, "cuda_oom")

        row = self._history_row(self.owner, generation_id)

        self.assertNotIn("frames", row["error_user_message"])
        self.assertIn("Try a lower resolution", row["error_user_message"])

    def test_the_stored_failure_is_unchanged(self):
        generation_id = self._failed(self.owner_id, "cuda_oom")

        self._history_row(self.owner, generation_id)
        stored = self.generation_repo.get_by_id(generation_id)

        self.assertEqual(stored.error_message, _admin_message("cuda_oom"))
        self.assertIn(TRACEBACK, stored.error_detail)

    def test_the_run_report_is_plain_for_its_regular_owner(self):
        generation_id = self._failed(self.owner_id, "cuda_oom")
        self.run_report_recorder.get_report.return_value = {
            "status_history": [{"step": "failed", "message": _admin_message("cuda_oom")}],
            "plugin_outputs": {},
        }

        user_report = asyncio.run(self.controller.get_run_report(generation_id, self.owner)).data["run_report"]
        admin_report = asyncio.run(self.controller.get_run_report(generation_id, self.admin)).data["run_report"]

        self.assertEqual(user_report["status_history"][0]["message"], "This was too much for the server's GPU right now.")
        self.assertEqual(admin_report["status_history"][0]["message"], _admin_message("cuda_oom"))
