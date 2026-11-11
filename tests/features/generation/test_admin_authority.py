from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from src.features.generation.dto import GenerationRequest
from src.features.generation.exceptions import AdminAuthorityRequired
from src.features.generation.pipeline_builder import BuiltPipeline
from src.features.generation.routes import GenerationController
from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.start_generation_tool import StartGenerationTool
from src.platform.security.api_scopes import ApiScope
from src.platform.security.principal import KeyGrant
from src.platform.security.user import AccountType, User
from tests.features.generation.test_orchestrator_fixed_prompt import (
    bind_form_passthrough,
    make_orchestrator,
    make_request,
    repo,
    start,
)


def person(account_type, user_id="u1"):
    return User(id=user_id, username=user_id, email=f"{user_id}@example.com", password_hash="x", account_type=account_type)


def key_user(owner, *scopes):
    grant = KeyGrant(key_id="k1", user_id=owner.id, scopes=frozenset(scopes), display_prefix="pui_aaaaaaaa")
    effective = owner.account_type if ApiScope.ADMIN in scopes else AccountType.USER
    return replace(owner, key=grant, account_type=effective)


def wire(orchestrator, owner_type, admin_only_preset):
    owner = SimpleNamespace(id="u1", account_type=owner_type)
    orchestrator.user_repository = Mock(get_by_id=Mock(return_value=owner))
    calls = []

    def build(**kwargs):
        calls.append(kwargs)
        used = admin_only_preset and not kwargs.get("acts_as_admin")
        return BuiltPipeline(
            generation_id="g", preset_id="p", preset_template=Mock(version="1"), pipes=[], needs_admin_authority=used,
        )

    orchestrator.pipeline_builder.build_pipeline = Mock(side_effect=build)
    return calls


class TestOrchestratorRefusesBeforeQueueing:
    @pytest.mark.asyncio
    async def test_an_admin_owner_without_authority_is_refused_on_an_admin_only_preset_and_nothing_is_queued(self, repo):
        orchestrator, _, _ = make_orchestrator()
        wire(orchestrator, AccountType.ADMIN, admin_only_preset=True)

        with pytest.raises(AdminAuthorityRequired):
            await orchestrator.start_generation(make_request(["x"]), "u1")

        repo.create.assert_not_called()
        orchestrator.backend_registry.select_backend_for_generation.return_value.start_generation.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_same_owner_with_authority_is_accepted_and_the_pipeline_is_built_with_it(self, repo):
        orchestrator, _, _ = make_orchestrator()
        calls = wire(orchestrator, AccountType.ADMIN, admin_only_preset=True)

        with patch("src.features.generation.orchestrator.generate_ulid", return_value="g2"):
            await orchestrator.start_generation(make_request([{"positive": "a", "negative": ""}]), "u1", acts_as_admin=True)

        assert calls and all(call["acts_as_admin"] is True for call in calls[-2:])

    @pytest.mark.asyncio
    async def test_an_admin_owner_without_authority_may_start_an_ordinary_preset(self, repo):
        orchestrator, _, _ = make_orchestrator()
        calls = wire(orchestrator, AccountType.ADMIN, admin_only_preset=False)

        await start(orchestrator, make_request([{"positive": "a", "negative": ""}]))

        assert calls and all(call["acts_as_admin"] is False for call in calls)

    @pytest.mark.asyncio
    async def test_a_non_admin_owner_is_never_probed_and_is_unchanged(self, repo):
        orchestrator, _, _ = make_orchestrator()
        calls = wire(orchestrator, AccountType.USER, admin_only_preset=True)

        await start(orchestrator, make_request([{"positive": "a", "negative": ""}]))

        assert all(call["acts_as_admin"] is False for call in calls)
        repo.create.assert_called()

    @pytest.mark.asyncio
    async def test_the_authority_a_request_was_started_with_travels_with_it_to_the_dispatch_build(self, repo):
        orchestrator, _, _ = make_orchestrator()
        calls = wire(orchestrator, AccountType.USER, admin_only_preset=False)
        request = make_request([{"positive": "a", "negative": ""}])

        with patch("src.features.generation.orchestrator.generate_ulid", return_value="g3"):
            await orchestrator.start_generation(request, "u1", acts_as_admin=True)

        assert request._acts_as_admin is True
        assert any(call.get("generation_id") == "g3" and call["acts_as_admin"] is True for call in calls)

    @pytest.mark.asyncio
    async def test_a_second_submission_never_inherits_the_first_ones_authority(self, repo):
        orchestrator, _, _ = make_orchestrator()
        wire(orchestrator, AccountType.USER, admin_only_preset=False)
        first = make_request([{"positive": "a", "negative": ""}])
        second = make_request([{"positive": "a", "negative": ""}])

        with patch("src.features.generation.orchestrator.generate_ulid", return_value="g4"):
            await orchestrator.start_generation(first, "u1", acts_as_admin=True)
        with patch("src.features.generation.orchestrator.generate_ulid", return_value="g5"):
            await orchestrator.start_generation(second, "u1")

        assert first._acts_as_admin is True and second._acts_as_admin is False

    @pytest.mark.asyncio
    async def test_a_call_site_that_forgets_to_carry_the_authority_fails_loudly(self):
        orchestrator, _, _ = make_orchestrator()
        wire(orchestrator, AccountType.ADMIN, admin_only_preset=False)
        forgetful = SimpleNamespace(preset_id="p")

        with pytest.raises(AttributeError):
            orchestrator._enforce_admin_authority(Mock(), "txt2img", SimpleNamespace(values={}, form_name=None), "u1", forgetful)

    def test_a_client_cannot_set_the_authority_through_the_request_body(self):
        request = GenerationRequest.model_validate({"preset_id": "p", "acts_as_admin": True, "_acts_as_admin": True})

        assert request._acts_as_admin is False
        assert "acts_as_admin" not in request.model_dump() and "_acts_as_admin" not in request.model_dump()


class TestProcessorTracksTheAuthorityTemplatesRead:
    def render(self, template_config, acts_as_admin):
        from src.features.presets.processor import PresetProcessor
        from src.platform.templating import TemplateProcessor
        from tests.features.presets.test_processor_context import _generation_data, _preset

        processor = PresetProcessor(
            template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
            user_resolver=lambda user_id: SimpleNamespace(account_type=AccountType.ADMIN),
        )
        probe = []
        pipes = processor.process(
            _preset(pipe_configuration=template_config), _generation_data(), user_id="u1",
            acts_as_admin=acts_as_admin, authority_probe=probe,
        )
        return pipes, probe

    def test_an_explicit_value_beats_the_owners_row(self):
        pipes, _ = self.render({"admin": "{{ runtime.user.is_admin }}"}, acts_as_admin=False)

        assert pipes[0]["config"]["admin"] is False

    def test_reading_it_is_recorded_and_not_reading_it_is_not(self):
        _, read = self.render({"admin": "{{ runtime.user.is_admin }}"}, acts_as_admin=False)
        _, untouched = self.render({"steps": 4}, acts_as_admin=False)

        assert read and not untouched

    def test_true_is_passed_through_when_the_caller_holds_authority(self):
        assert self.render({"admin": "{{ runtime.user.is_admin }}"}, acts_as_admin=True)[0][0]["config"]["admin"] is True


class TestBuilderReportsWhatThePipelineAsksFor:
    def build(self, config, acts_as_admin):
        from src.features.generation.pipeline_builder import PipelineBuilder
        from src.features.presets.processor import PresetProcessor
        from src.platform.templating import TemplateProcessor
        from tests.features.presets.test_processor_context import _preset

        processor = PresetProcessor(
            template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
        )
        builder = PipelineBuilder(Mock(), processor)
        return builder.build_pipeline(
            preset_id=_preset(pipe_configuration=config), form_data={}, mode="txt2img",
            user_id="u1", acts_as_admin=acts_as_admin,
        )

    @pytest.mark.parametrize("acts", [False, True])
    def test_a_template_that_reads_the_authority_is_flagged_whatever_its_value(self, acts):
        built = self.build({"admin": "{{ runtime.user.is_admin }}"}, acts)

        assert built.needs_admin_authority is True
        assert built.pipes[0]["config"]["admin"] is acts

    def test_an_ordinary_template_is_not_flagged(self):
        assert self.build({"steps": 4}, False).needs_admin_authority is False


class TestControllerDerivesAuthorityFromTheCaller:
    def controller(self):
        controller = object.__new__(GenerationController)
        controller.generation_orchestrator = Mock()
        controller.generation_orchestrator.start_generation = AsyncMock(
            return_value={"generation_id": "g", "status": {"status": "pending"}}
        )
        controller.generation_orchestrator.preview_memory = AsyncMock(return_value={})
        controller.output_broadcaster = Mock()
        controller.logger = Mock()
        return controller

    @pytest.mark.asyncio
    @pytest.mark.parametrize("label,caller,expected", [
        ("an admin session", person(AccountType.ADMIN), True),
        ("a plain user", person(AccountType.USER), False),
        ("a key with admin", key_user(person(AccountType.ADMIN), ApiScope.GENERATE, ApiScope.ADMIN), True),
        ("an admin owner's key without admin", key_user(person(AccountType.ADMIN), ApiScope.GENERATE), False),
        ("a plain user's key", key_user(person(AccountType.USER), ApiScope.GENERATE), False),
    ])
    async def test_start_and_preview_carry_the_effective_authority(self, label, caller, expected):
        controller = self.controller()
        request = GenerationRequest(preset_id="p")

        await controller.start_generation(request, caller)
        await controller.preview_memory(request, caller)

        start_kwargs = controller.generation_orchestrator.start_generation.call_args.kwargs
        preview_kwargs = controller.generation_orchestrator.preview_memory.call_args.kwargs
        assert start_kwargs.get("acts_as_admin", False) is expected, label
        assert preview_kwargs.get("acts_as_admin", False) is expected, label

    @pytest.mark.asyncio
    async def test_a_refusal_is_a_403_insufficient_scope_naming_admin(self):
        controller = self.controller()
        controller.generation_orchestrator.start_generation.side_effect = AdminAuthorityRequired("restricted")

        with pytest.raises(HTTPException) as refused:
            await controller.start_generation(GenerationRequest(preset_id="p"), key_user(person(AccountType.ADMIN), ApiScope.GENERATE))

        assert refused.value.status_code == 403
        assert refused.value.detail["error"] == "insufficient_scope"
        assert refused.value.headers["WWW-Authenticate"] == 'Bearer error="insufficient_scope", scope="admin"'

    @pytest.mark.asyncio
    async def test_grid_cells_and_their_retries_are_started_as_the_calling_user(self):
        from src.features.generation.grids import routes as grid_routes

        started = []

        class Controller:
            async def start_generation(self, request, user, grid_cell=None):
                started.append(user)
                return SimpleNamespace(data={"generation_id": "g"})

        container = SimpleNamespace(
            _generation_controller=Controller(), generation_history_facade=Mock(), preset_template_loader=Mock(),
            settings=Mock(), generation_orchestrator=Mock(), limit_guard=None,
        )
        service = grid_routes._service(container)
        retrying_caller = key_user(person(AccountType.ADMIN, "root"), ApiScope.GENERATE)

        await service.submit(GenerationRequest(preset_id="p"), retrying_caller, Mock())

        assert started == [retrying_caller]


class TestToolContextsCarryTheAuthority:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("acts,expected", [(None, False), (False, False), (True, True)])
    async def test_the_start_tool_hands_the_orchestrator_the_contexts_authority(self, acts, expected):
        orchestrator = AsyncMock()
        orchestrator.start_generation.return_value = {"generation_id": "g", "status": {"status": "pending"}}
        fields = {} if acts is None else {"acts_as_admin": acts}
        context = ToolContext(user_id="u1", is_admin=True, generation_orchestrator=orchestrator, **fields)

        await StartGenerationTool().execute_confirmed(context, preset_id="sdxl/base", prompt="a fox")

        assert orchestrator.start_generation.call_args.kwargs["acts_as_admin"] is expected

    def test_a_chat_session_of_an_admin_acts_as_one_and_a_plain_user_does_not(self):
        from src.features.chat.tool_dispatcher import ToolCallDispatcher
        from src.features.llm.tools.base import ToolServices

        dispatcher = object.__new__(ToolCallDispatcher)
        dispatcher._m = SimpleNamespace(
            tool_services=ToolServices(), llm_service=SimpleNamespace(repository=None), plugins=None,
        )
        session = SimpleNamespace(mode="generation", llm_config_id=None)

        admin = dispatcher.tool_context(session, "u1", True, None)
        plain = dispatcher.tool_context(session, "u2", False, None)

        assert admin.acts_as_admin is True and plain.acts_as_admin is False
