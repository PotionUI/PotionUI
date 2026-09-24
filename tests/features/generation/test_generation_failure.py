import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.failure import (
    DETAIL_LIMIT_CHARS,
    GenerationFailure,
    apply_failure,
    cap_detail,
    failure_from_exception,
    failure_from_output,
)
from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository
from src.features.generation.routes import GenerationController, build_router
from src.features.generation.status_tracker import GenerationState, GenerationStatusTracker
from src.pipelines.outputs import ErrorGenerationOutput
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from src.platform.util.ids import generate_ulid
from src.platform.websocket.connection_hub import ConnectionHub

RAW_ERROR = "FileNotFoundError: [Errno 2] No such file or directory: '/srv/models/private/unet.safetensors'"
TRACEBACK = 'Traceback (most recent call last):\n  File "/srv/app/src/pipelines/pipes/loader.py", line 42, in process\n'
LEAK_MARKERS = ("/srv/", "Traceback", "Errno", "FileNotFoundError", "loader.py")


def _assert_no_leak(payload):
    rendered = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    for marker in LEAK_MARKERS:
        assert marker not in rendered, marker


def _error_output():
    output = ErrorGenerationOutput(
        error=RAW_ERROR,
        detail=TRACEBACK,
        pipe_key="loader",
        failed_at_step="Loading 1/2",
    )
    output.pipe_id = 1
    output.pipe_name = "model_loader"
    return output


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


class TestFailureModel:
    def test_output_failure_keeps_raw_text_out_of_the_safe_fields(self):
        failure = failure_from_output(_error_output())

        assert failure.error_code == "missing_model_file"
        assert failure.message == "A model file this preset needs is missing."
        _assert_no_leak(failure.message)
        _assert_no_leak(failure.hint)
        _assert_no_leak(failure.user_message)
        assert failure.user_message.startswith(failure.message)
        assert failure.hint in failure.user_message
        assert RAW_ERROR in failure.stored_detail
        assert TRACEBACK in failure.stored_detail
        assert failure.failed_pipe_id == "loader"
        assert failure.failed_pipe_name == "model_loader"
        assert failure.failed_at_step == "Loading 1/2"

    def test_an_unclassified_output_gets_the_generic_message_and_hint(self):
        failure = failure_from_output(ErrorGenerationOutput(error="KeyError: 'api_key=sk-123' in /etc/potion/secrets.yml"))

        assert failure.error_code == "unclassified"
        assert failure.message == "Something went wrong while generating."
        assert "error ID" in failure.hint
        assert "sk-123" not in failure.user_message
        assert "/etc/" not in failure.user_message

    def test_the_engine_supplied_classification_wins_over_text_matching(self):
        output = ErrorGenerationOutput(
            error="RuntimeError: weird",
            error_code="cuda_oom",
            message="Ran out of GPU memory (VRAM) during generation. (0.5GB free of 24.0GB total VRAM)",
            hints=["Lower the resolution one tier"],
        )

        failure = failure_from_output(output)

        assert failure.error_code == "cuda_oom"
        assert failure.message.endswith("(0.5GB free of 24.0GB total VRAM)")
        assert failure.hints == ("Lower the resolution one tier",)

    def test_exception_failure_records_the_type_and_the_traceback(self):
        failure = failure_from_exception(ValueError("/srv/private/form.json is broken"), TRACEBACK)

        assert failure.error_code == "unclassified"
        _assert_no_leak(failure.user_message)
        assert "ValueError: /srv/private/form.json is broken" in failure.stored_detail
        assert TRACEBACK in failure.stored_detail

    def test_stored_detail_is_capped(self):
        failure = GenerationFailure(error_code="unclassified", message="m", raw_error="a" * 50_000, detail="z" * 50_000)

        stored = failure.stored_detail

        assert len(stored) < DETAIL_LIMIT_CHARS + 100
        assert stored.startswith("a" * 100)
        assert stored.endswith("z" * 100)
        assert "characters truncated" in stored

    def test_cap_detail_leaves_short_text_alone(self):
        assert cap_detail("short") == "short"

    def test_apply_failure_writes_only_the_safe_fields_onto_the_output(self):
        output = _error_output()

        apply_failure(output, failure_from_output(output))

        assert output.error_code == "missing_model_file"
        assert output.message == "A model file this preset needs is missing."
        assert output.error == RAW_ERROR


class _Socket:
    def __init__(self):
        self.sent = []

    async def accept(self):
        return None

    async def send_text(self, text):
        self.sent.append(text)


class TestWebSocketPerRecipient:
    @pytest.fixture
    def controller(self):
        run_report_recorder = Mock()
        controller = GenerationController(Mock(), Mock(), Mock(), run_report_recorder)
        return controller

    async def _subscribe(self, hub, client_id, generation_id, privileged):
        socket = _Socket()
        await hub.connect(socket, client_id)
        await hub.subscribe_to_generation(client_id, generation_id, privileged=privileged)
        return socket

    @pytest.mark.asyncio
    async def test_a_regular_subscriber_never_receives_the_detail_and_an_admin_does(self, controller):
        hub = controller.connection_hub
        user_socket = await self._subscribe(hub, "user-client", "gen-1", privileged=False)
        admin_socket = await self._subscribe(hub, "admin-client", "gen-1", privileged=True)
        output = _error_output()
        apply_failure(output, failure_from_output(output))

        await controller._broadcast_generation_output("gen-1", output, SimpleNamespace(preset_id="p1"))

        user_message = json.loads(user_socket.sent[-1])
        admin_message = json.loads(admin_socket.sent[-1])

        assert user_message["type"] == "generation_error"
        assert user_message["error_code"] == "missing_model_file"
        assert user_message["message"] == "A model file this preset needs is missing."
        assert user_message["hint"]
        assert user_message["error_id"] == "gen-1"
        assert user_message["generation_id"] == "gen-1"
        assert user_message["pipe_id"] == 1
        for key in ("detail", "error", "failed_pipe_id", "failed_pipe_name", "failed_at_step"):
            assert key not in user_message
        _assert_no_leak(user_socket.sent[-1])

        assert admin_message["error_code"] == "missing_model_file"
        assert admin_message["message"] == user_message["message"]
        assert RAW_ERROR in admin_message["detail"]
        assert TRACEBACK in admin_message["detail"]
        assert admin_message["failed_pipe_id"] == "loader"
        assert admin_message["failed_pipe_name"] == "model_loader"
        assert admin_message["failed_at_step"] == "Loading 1/2"

    @pytest.mark.asyncio
    async def test_the_run_report_records_only_the_safe_message(self, controller):
        await self._subscribe(controller.connection_hub, "c1", "gen-1", privileged=True)
        output = _error_output()

        await controller._broadcast_generation_output("gen-1", output, SimpleNamespace(preset_id="p1"))

        recorded = controller.run_report_recorder.record_output.call_args[0][1]
        assert recorded["type"] == "generation_error"
        _assert_no_leak(recorded)

    @pytest.mark.asyncio
    async def test_a_non_error_output_reaches_admins_unchanged(self, controller):
        from src.pipelines.outputs import ProgressGenerationOutput

        hub = controller.connection_hub
        user_socket = await self._subscribe(hub, "u", "gen-1", privileged=False)
        admin_socket = await self._subscribe(hub, "a", "gen-1", privileged=True)

        await controller._broadcast_generation_output("gen-1", ProgressGenerationOutput(state="x"), SimpleNamespace(preset_id="p1"))

        assert user_socket.sent[-1] == admin_socket.sent[-1]


class TestConnectionHubPrivilege:
    @pytest.mark.asyncio
    async def test_privileged_message_goes_only_to_privileged_clients(self):
        hub = ConnectionHub()
        user_socket, admin_socket = _Socket(), _Socket()
        await hub.connect(user_socket, "u")
        await hub.connect(admin_socket, "a")
        await hub.subscribe_to_generation("u", "g", privileged=False)
        await hub.subscribe_to_generation("a", "g", privileged=True)

        await hub.broadcast_to_generation("g", {"v": "public"}, {"v": "private"})

        assert json.loads(user_socket.sent[-1]) == {"v": "public"}
        assert json.loads(admin_socket.sent[-1]) == {"v": "private"}

    @pytest.mark.asyncio
    async def test_disconnect_forgets_the_privilege(self):
        hub = ConnectionHub()
        await hub.connect(_Socket(), "a")
        await hub.subscribe_to_generation("a", "g", privileged=True)

        hub.disconnect("a")

        assert "a" not in hub.privileged_clients


class TestStatusRecord:
    def test_a_failed_live_record_exposes_only_safe_fields(self):
        tracker = GenerationStatusTracker()
        tracker.create(id="gen-1", user_id="u1")

        with patch("src.features.generation.status_tracker.generation_repo"):
            tracker.transition("gen-1", GenerationState.FAILED, failure_from_output(_error_output()))

        dumped = tracker.get("gen-1").model_dump()

        assert dumped["status"] == "failed"
        assert dumped["error_code"] == "missing_model_file"
        assert dumped["error_id"] == "gen-1"
        assert dumped["message"] == "A model file this preset needs is missing."
        _assert_no_leak(dumped)


class TestFailureRest(PersistenceTestBase):
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
        self.controller = GenerationController(self.orchestrator, self.history_facade, Mock(), run_report_recorder)
        self.owner_id = self.create_test_user("owner-1", "owner", "owner@example.com")
        self.owner = _user(AccountType.USER, self.owner_id)
        self.admin = _user(AccountType.ADMIN, "admin-1")
        self.failed = self._generation("failed")
        self.generation_repo.update_status(self.failed.id, "failed", failure=failure_from_output(_error_output()).columns())
        self.completed = self._generation("completed")

    def _generation(self, status):
        return self.generation_repo.create(Generation(
            id=generate_ulid(),
            preset_id="preset-1",
            form_data={"prompt": "a cat"},
            user_id=self.owner_id,
            status=status,
        ))

    def _client(self, user):
        app = FastAPI()
        app.include_router(build_router(SimpleNamespace(_generation_controller=self.controller)))
        app.dependency_overrides[get_current_active_user] = lambda: user
        return TestClient(app)

    def test_status_of_a_failed_generation_is_safe_for_its_owner(self):
        response = asyncio.run(self.controller.get_generation_status(self.failed.id, self.owner))

        data = response.data
        assert data["status"] == "failed"
        assert data["error_code"] == "missing_model_file"
        assert data["error_id"] == self.failed.id
        assert data["error_message"] == "A model file this preset needs is missing."
        assert data["error_user_message"].startswith("A model file this preset needs is missing.")
        _assert_no_leak(data)

    def test_history_detail_of_a_failed_generation_is_safe_for_its_owner(self):
        data = self.history_facade.get_by_id(self.failed.id, self.owner_id)

        assert data["error_code"] == "missing_model_file"
        assert data["error_id"] == self.failed.id
        _assert_no_leak(data)

    def test_history_list_of_a_failed_generation_is_safe_for_its_owner(self):
        response = self._client(self.owner).get("/api/generations/history")

        assert response.status_code == 200
        rows = response.json()["data"]["generations"]
        row = next(r for r in rows if r["id"] == self.failed.id)
        assert row["error_code"] == "missing_model_file"
        _assert_no_leak(response.text)

    def test_the_failure_endpoint_is_forbidden_to_the_owner(self):
        response = self._client(self.owner).get(f"/api/generations/{self.failed.id}/failure")

        assert response.status_code == 403
        _assert_no_leak(response.text)

    def test_the_failure_endpoint_gives_an_admin_the_full_detail(self):
        response = self._client(self.admin).get(f"/api/generations/{self.failed.id}/failure")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["error_code"] == "missing_model_file"
        assert data["message"] == "A model file this preset needs is missing."
        assert "Re-download the model" in data["hint"]
        assert RAW_ERROR in data["detail"]
        assert TRACEBACK in data["detail"]
        assert data["failed_pipe_id"] == "loader"
        assert data["failed_pipe_name"] == "model_loader"
        assert data["failed_at_step"] == "Loading 1/2"
        assert data["occurred_at"]

    def test_the_failure_endpoint_404s_for_a_generation_that_did_not_fail(self):
        with pytest.raises(HTTPException) as raised:
            asyncio.run(self.controller.get_generation_failure(self.completed.id))

        assert raised.value.status_code == 404

    def test_the_failure_endpoint_404s_for_an_unknown_generation(self):
        response = self._client(self.admin).get("/api/generations/no-such-id/failure")

        assert response.status_code == 404
