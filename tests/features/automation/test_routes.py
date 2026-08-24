"""Tests for AutomationController (A7 wiring layer)."""
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, Mock

from src.features.automation.routes import AutomationController, build_router, build_ws_router
from src.features.automation.dto import (
    CreateAutomationRequest,
    ImportAutomationRequest,
    InstantiateAutomationTemplateRequest,
    RunNowRequest,
    UpdateAutomationRequest,
    ValidateGraphRequest,
)
from src.features.automation.manager import (
    AutomationImportError,
    AutomationTemplateNotFoundError,
    AutomationTemplateUnavailableError,
    GraphValidationError,
)
from src.platform.plugins.automation_nodes import NodeField, NodeTypeRegistry, NodeTypeSpec
from src.features.automation.records import Automation, AutomationRun, AutomationRunNode
from src.platform.security.user import AccountType, User


class TestAutomationController:
    """Comprehensive tests for AutomationController."""

    @pytest.fixture
    def mock_manager(self):
        manager = Mock()
        manager.create = AsyncMock()
        manager.update = AsyncMock()
        manager.delete = AsyncMock()
        manager.set_enabled = AsyncMock()
        manager.run_now = AsyncMock()
        manager.instantiate_template = AsyncMock()
        manager.repository = Mock()
        return manager

    @pytest.fixture
    def registry(self):
        return NodeTypeRegistry()

    @pytest.fixture
    def controller(self, mock_manager, registry):
        return AutomationController(mock_manager, registry=registry)

    @pytest.fixture
    def sample_user(self):
        user = Mock(spec=User)
        user.id = "user-123"
        return user

    @pytest.fixture
    def sample_automation(self):
        return Automation(
            id="auto-1", name="Test Automation",
            graph={"nodes": [], "edges": []}, enabled=False,
        )

    # ========== CRUD ==========

    @pytest.mark.asyncio
    async def test_list_automations(self, controller, mock_manager, sample_user, sample_automation):
        mock_manager.list.return_value = [sample_automation]

        result = await controller.list_automations(sample_user)

        assert result.success is True
        assert len(result.data) == 1
        assert result.data[0]["id"] == "auto-1"

    @pytest.mark.asyncio
    async def test_create_automation_success(self, controller, mock_manager, sample_user, sample_automation):
        mock_manager.create.return_value = sample_automation

        request = CreateAutomationRequest(name="Test Automation", graph={"nodes": [], "edges": []})
        result = await controller.create_automation(request, sample_user)

        assert result.success is True
        assert result.data["id"] == "auto-1"
        mock_manager.create.assert_called_once_with(
            name="Test Automation", graph={"nodes": [], "edges": []},
            description=None, user_id="user-123", enabled=False,
        )

    @pytest.mark.asyncio
    async def test_create_automation_invalid_graph(self, controller, mock_manager, sample_user):
        mock_manager.create.side_effect = GraphValidationError(
            [{"node_id": None, "message": "Graph contains a cycle", "severity": "error"}]
        )

        request = CreateAutomationRequest(name="Bad", graph={"nodes": [], "edges": []})
        result = await controller.create_automation(request, sample_user)

        assert result.success is False
        assert result.error == "invalid_graph"

    @pytest.mark.asyncio
    async def test_get_automation_found(self, controller, mock_manager, sample_user, sample_automation):
        mock_manager.get.return_value = sample_automation

        result = await controller.get_automation("auto-1", sample_user)

        assert result.success is True
        assert result.data["name"] == "Test Automation"

    @pytest.mark.asyncio
    async def test_get_automation_not_found(self, controller, mock_manager, sample_user):
        mock_manager.get.return_value = None

        result = await controller.get_automation("missing", sample_user)

        assert result.success is False
        assert result.error == "automation_not_found"

    @pytest.mark.asyncio
    async def test_update_automation_success(self, controller, mock_manager, sample_user, sample_automation):
        mock_manager.update.return_value = sample_automation

        request = UpdateAutomationRequest(name="Renamed")
        result = await controller.update_automation("auto-1", request, sample_user)

        assert result.success is True
        mock_manager.update.assert_called_once_with(
            "auto-1", name="Renamed", graph=None, description=None, enabled=None
        )

    @pytest.mark.asyncio
    async def test_update_automation_not_found(self, controller, mock_manager, sample_user):
        mock_manager.update.return_value = None

        request = UpdateAutomationRequest(name="Renamed")
        result = await controller.update_automation("missing", request, sample_user)

        assert result.success is False
        assert result.error == "automation_not_found"

    @pytest.mark.asyncio
    async def test_update_automation_invalid_graph(self, controller, mock_manager, sample_user):
        mock_manager.update.side_effect = GraphValidationError(
            [{"node_id": "n1", "message": "Unknown node type: 'bogus'", "severity": "error"}]
        )

        request = UpdateAutomationRequest(graph={"nodes": [{"id": "n1", "type": "bogus"}], "edges": []})
        result = await controller.update_automation("auto-1", request, sample_user)

        assert result.success is False
        assert result.error == "invalid_graph"

    @pytest.mark.asyncio
    async def test_enable_automation(self, controller, mock_manager, sample_user, sample_automation):
        sample_automation.enabled = True
        mock_manager.set_enabled.return_value = sample_automation

        result = await controller.set_enabled("auto-1", True, sample_user)

        assert result.success is True
        mock_manager.set_enabled.assert_called_once_with("auto-1", True)

    @pytest.mark.asyncio
    async def test_disable_automation_not_found(self, controller, mock_manager, sample_user):
        mock_manager.set_enabled.return_value = None

        result = await controller.set_enabled("missing", False, sample_user)

        assert result.success is False
        assert result.error == "automation_not_found"

    @pytest.mark.asyncio
    async def test_delete_automation_success(self, controller, mock_manager, sample_user):
        mock_manager.delete.return_value = True

        result = await controller.delete_automation("auto-1", sample_user)

        assert result.success is True
        assert result.data["id"] == "auto-1"

    @pytest.mark.asyncio
    async def test_delete_automation_not_found(self, controller, mock_manager, sample_user):
        mock_manager.delete.return_value = False

        result = await controller.delete_automation("missing", sample_user)

        assert result.success is False
        assert result.error == "automation_not_found"

    # ========== Run / Validate ==========

    @pytest.mark.asyncio
    async def test_run_now_success(self, controller, mock_manager, sample_user):
        mock_manager.run_now.return_value = "run-1"

        request = RunNowRequest()
        result = await controller.run_now("auto-1", request, sample_user)

        assert result.success is True
        assert result.data["run_id"] == "run-1"
        mock_manager.run_now.assert_called_once_with("auto-1", node_id=None, payload=None)

    @pytest.mark.asyncio
    async def test_run_now_not_found(self, controller, mock_manager, sample_user):
        mock_manager.run_now.return_value = None

        request = RunNowRequest()
        result = await controller.run_now("missing", request, sample_user)

        assert result.success is False
        assert result.error == "run_failed"

    @pytest.mark.asyncio
    async def test_validate_graph(self, controller, mock_manager, sample_user):
        mock_manager.validate_graph.return_value = [
            {"node_id": None, "message": "Graph contains a cycle", "severity": "error"}
        ]

        request = ValidateGraphRequest(graph={"nodes": [], "edges": []})
        result = await controller.validate_graph(request, sample_user)

        assert result.success is True
        assert len(result.data["issues"]) == 1
        assert result.data["issues"][0]["severity"] == "error"

    # ========== Node types catalog ==========

    @pytest.mark.asyncio
    async def test_node_types_catalog_shape(self, controller, registry, sample_user):
        async def _noop(ctx):
            return None

        registry.register(NodeTypeSpec(
            key="action.test", kind="action", title="Test Action", description="desc",
            icon="bolt", category="general",
            config_schema=[
                {"name": "path", "type": "string", "label": "Path", "default": "{{ event.path }}"},
                {"name": "count", "type": "number", "label": "Count", "required": True},
            ],
            execute=_noop,
        ))

        result = await controller.get_node_types(sample_user)

        assert result.success is True
        assert len(result.data) == 1
        node_type = result.data[0]
        assert node_type["key"] == "action.test"
        assert node_type["kind"] == "action"
        assert node_type["output_ports"] == ["out"]

        # Flat {"properties": {name: config}} shape - not the raw internal list.
        properties = node_type["config_schema"]["properties"]
        assert set(properties.keys()) == {"path", "count"}
        assert properties["path"]["type"] == "string"
        assert properties["path"]["default"] == "{{ event.path }}"
        assert properties["count"]["required"] is True
        # "name" itself shouldn't be duplicated inside the field config
        assert "name" not in properties["path"]

    @pytest.mark.asyncio
    async def test_node_types_condition_has_true_false_ports(self, controller, registry, sample_user):
        registry.register(NodeTypeSpec(
            key="condition.test", kind="condition", title="Test Condition",
            config_schema=[],
        ))

        result = await controller.get_node_types(sample_user)

        assert result.data[0]["output_ports"] == ["true", "false"]

    async def test_node_types_dynamic_ports_config_key_included_when_set(self, controller, registry, sample_user):
        registry.register(NodeTypeSpec(
            key="condition.switch", kind="condition", title="Switch",
            output_ports=("default",), dynamic_ports_config_key="cases",
            config_schema=[{"name": "cases", "type": "textbox", "title": "Cases"}],
        ))

        result = await controller.get_node_types(sample_user)

        assert result.data[0]["dynamic_ports_config_key"] == "cases"
        # Static fallback ports still present alongside the dynamic-ports signal.
        assert result.data[0]["output_ports"] == ["default"]

    async def test_node_types_dynamic_ports_config_key_omitted_when_unset(self, controller, registry, sample_user):
        registry.register(NodeTypeSpec(
            key="condition.test", kind="condition", title="Test Condition",
            config_schema=[],
        ))

        result = await controller.get_node_types(sample_user)

        assert "dynamic_ports_config_key" not in result.data[0]

    # ========== Run history ==========

    @pytest.mark.asyncio
    async def test_list_runs(self, controller, mock_manager, sample_user):
        run = AutomationRun(id="run-1", automation_id="auto-1", status="success")
        mock_manager.repository.list_runs.return_value = [run]

        result = await controller.list_runs("auto-1", sample_user, limit=10, before=None)

        assert result.success is True
        assert len(result.data) == 1
        assert result.data[0]["id"] == "run-1"
        mock_manager.repository.list_runs.assert_called_once_with("auto-1", limit=10, before=None)

    @pytest.mark.asyncio
    async def test_get_run_includes_nodes(self, controller, mock_manager, sample_user):
        run = AutomationRun(id="run-1", automation_id="auto-1", status="success")
        run_node = AutomationRunNode(id="rn-1", run_id="run-1", node_id="n1", node_type="action.test", status="success")
        mock_manager.repository.get_run.return_value = run
        mock_manager.repository.list_run_nodes.return_value = [run_node]

        result = await controller.get_run("auto-1", "run-1", sample_user)

        assert result.success is True
        assert result.data["id"] == "run-1"
        assert len(result.data["nodes"]) == 1
        assert result.data["nodes"][0]["node_id"] == "n1"

    @pytest.mark.asyncio
    async def test_get_run_not_found(self, controller, mock_manager, sample_user):
        mock_manager.repository.get_run.return_value = None

        result = await controller.get_run("auto-1", "missing", sample_user)

        assert result.success is False
        assert result.error == "run_not_found"

    @pytest.mark.asyncio
    async def test_get_run_wrong_automation(self, controller, mock_manager, sample_user):
        run = AutomationRun(id="run-1", automation_id="other-auto", status="success")
        mock_manager.repository.get_run.return_value = run

        result = await controller.get_run("auto-1", "run-1", sample_user)

        assert result.success is False
        assert result.error == "run_not_found"

    # ========== Frontend contract lock (per team-lead) ==========

    def test_validate_graph_request_shape(self):
        """POST /api/automations/validate body is {graph: <graph JSON>} - not tied to a saved automation id."""
        assert set(ValidateGraphRequest.model_fields.keys()) == {"graph"}
        request = ValidateGraphRequest(graph={"nodes": [], "edges": []})
        assert request.graph == {"nodes": [], "edges": []}

    def test_run_now_request_shape(self):
        """POST /api/automations/{id}/run body is {node_id?, payload?} - both optional."""
        assert set(RunNowRequest.model_fields.keys()) == {"node_id", "payload"}
        request = RunNowRequest()
        assert request.node_id is None
        assert request.payload is None

    @pytest.mark.asyncio
    async def test_run_now_passes_node_id_and_payload_through(self, controller, mock_manager, sample_user):
        mock_manager.run_now.return_value = "run-1"

        request = RunNowRequest(node_id="n1", payload={"foo": "bar"})
        await controller.run_now("auto-1", request, sample_user)

        mock_manager.run_now.assert_called_once_with("auto-1", node_id="n1", payload={"foo": "bar"})

    @pytest.fixture
    def built_router(self, controller):
        return build_router(SimpleNamespace(automation_controller=controller))

    @pytest.fixture
    def built_ws_router(self, controller):
        return build_ws_router(SimpleNamespace(automation_controller=controller))

    def test_routes_registered_with_expected_paths_and_methods(self, built_router):
        """Locks the exact route surface the frontend codes against, including the
        node-types/validate-before-{automation_id} ordering gotcha."""
        route_signatures = {
            (route.path, method)
            for route in built_router.routes
            for method in route.methods
        }
        expected = {
            ("/api/automations/templates", "GET"),
            ("/api/automations/templates/{template_key}/instantiate", "POST"),
            ("/api/automations/node-types", "GET"),
            ("/api/automations/validate", "POST"),
            ("/api/automations/", "GET"),
            ("/api/automations/", "POST"),
            ("/api/automations/{automation_id}", "GET"),
            ("/api/automations/{automation_id}", "PUT"),
            ("/api/automations/{automation_id}", "DELETE"),
            ("/api/automations/{automation_id}/enable", "PATCH"),
            ("/api/automations/{automation_id}/disable", "PATCH"),
            ("/api/automations/{automation_id}/run", "POST"),
            ("/api/automations/{automation_id}/runs", "GET"),
            ("/api/automations/{automation_id}/runs/{run_id}", "GET"),
        }
        assert expected <= route_signatures

        # /node-types and /validate must come before /{automation_id} in
        # declaration order so FastAPI doesn't swallow them as path params.
        paths_in_order = [route.path for route in built_router.routes]
        node_types_idx = paths_in_order.index("/api/automations/node-types")
        templates_idx = paths_in_order.index("/api/automations/templates")
        validate_idx = paths_in_order.index("/api/automations/validate")
        automation_id_idx = paths_in_order.index("/api/automations/{automation_id}")
        assert templates_idx < automation_id_idx
        assert node_types_idx < automation_id_idx
        assert validate_idx < automation_id_idx

    def test_import_route_declared_before_the_automation_id_family(self, built_router):
        paths_in_order = [route.path for route in built_router.routes]
        import_idx = paths_in_order.index("/api/automations/import")
        automation_id_idx = paths_in_order.index("/api/automations/{automation_id}")
        assert import_idx < automation_id_idx

    def test_export_and_import_routes_registered(self, built_router):
        registered = {(route.path, method) for route in built_router.routes for method in route.methods}
        assert ("/api/automations/import", "POST") in registered
        assert ("/api/automations/{automation_id}/export", "GET") in registered

    def test_ws_route_registered_at_expected_path(self, built_ws_router):
        ws_paths = {route.path for route in built_ws_router.routes}
        assert "/ws/automations" in ws_paths

    # ========== Output contract in the catalog ==========

    @pytest.mark.asyncio
    async def test_node_types_catalog_serializes_declared_outputs(self, controller, registry, sample_user):
        async def _noop(ctx):
            return None

        registry.register(NodeTypeSpec(
            key="action.emits", kind="action", title="Emits",
            config_schema=[{"name": "path", "type": "string", "templatable": True}],
            outputs=(NodeField("model_id", "string", "Model ID", "The id.", "m1"),),
            execute=_noop,
        ))

        result = await controller.get_node_types(sample_user)
        node_type = result.data[0]

        assert node_type["outputs"] == [{
            "key": "model_id", "type": "string", "label": "Model ID",
            "description": "The id.", "example": "m1",
        }]
        # A node with a knowable payload must not be flagged dynamic.
        assert "dynamic_outputs" not in node_type
        # `templatable` rides along on the field def with no serializer changes.
        assert node_type["config_schema"]["properties"]["path"]["templatable"] is True

    @pytest.mark.asyncio
    async def test_node_types_catalog_flags_dynamic_outputs(self, controller, registry, sample_user):
        registry.register(NodeTypeSpec(
            key="trigger.whatever", kind="trigger", title="Whatever", dynamic_outputs=True,
        ))

        result = await controller.get_node_types(sample_user)
        node_type = result.data[0]

        assert node_type["outputs"] == []
        assert node_type["dynamic_outputs"] is True

    # ========== Export / import ==========

    @pytest.mark.asyncio
    async def test_list_templates(self, controller, mock_manager, sample_user):
        mock_manager.list_templates.return_value = [
            {
                "key": "core:starter",
                "title": "Starter",
                "available": True,
                "missing_node_types": [],
            }
        ]

        result = await controller.list_templates(sample_user)

        assert result.success is True
        assert result.data[0]["key"] == "core:starter"
        mock_manager.list_templates.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_instantiate_template_returns_disabled_copy_and_warnings(
        self, controller, mock_manager, sample_user, sample_automation
    ):
        warnings = [
            {
                "node_id": "watch",
                "message": "directory missing",
                "severity": "error",
                "category": "environment",
            }
        ]
        mock_manager.instantiate_template.return_value = (sample_automation, warnings)

        result = await controller.instantiate_template(
            "plugin:example:starter",
            InstantiateAutomationTemplateRequest(name="My copy"),
            sample_user,
        )

        assert result.success is True
        assert result.data["automation"]["id"] == "auto-1"
        assert result.data["warnings"] == warnings
        mock_manager.instantiate_template.assert_awaited_once_with(
            "plugin:example:starter",
            user_id="user-123",
            name="My copy",
        )

    @pytest.mark.asyncio
    async def test_instantiate_missing_template_returns_specific_error(
        self, controller, mock_manager, sample_user
    ):
        mock_manager.instantiate_template.side_effect = AutomationTemplateNotFoundError("missing")

        result = await controller.instantiate_template(
            "missing",
            InstantiateAutomationTemplateRequest(),
            sample_user,
        )

        assert result.success is False
        assert result.error == "template_not_found"

    @pytest.mark.asyncio
    async def test_instantiate_unavailable_template_names_requirement_error(
        self, controller, mock_manager, sample_user
    ):
        mock_manager.instantiate_template.side_effect = AutomationTemplateUnavailableError(
            "Template requires node type(s) not installed on this system: action.plugin"
        )

        result = await controller.instantiate_template(
            "plugin:example:starter",
            InstantiateAutomationTemplateRequest(),
            sample_user,
        )

        assert result.success is False
        assert result.error == "template_unavailable"
        assert "action.plugin" in result.message

    @pytest.mark.asyncio
    async def test_export_automation(self, controller, mock_manager, sample_user):
        mock_manager.export_automation.return_value = {"schema": "potionui.automation"}

        result = await controller.export_automation("auto-1", sample_user)

        assert result.success is True
        assert result.data["schema"] == "potionui.automation"
        mock_manager.export_automation.assert_called_once_with("auto-1")

    @pytest.mark.asyncio
    async def test_export_missing_automation_returns_not_found(self, controller, mock_manager, sample_user):
        mock_manager.export_automation.return_value = None

        result = await controller.export_automation("nope", sample_user)

        assert result.success is False
        assert result.error == "automation_not_found"

    @pytest.mark.asyncio
    async def test_import_automation_returns_automation_and_warnings(
        self, controller, mock_manager, sample_user, sample_automation
    ):
        warnings = [{"node_id": "fs_1", "message": "no such dir", "severity": "error", "category": "environment"}]
        mock_manager.import_automation = AsyncMock(return_value=(sample_automation, warnings))

        result = await controller.import_automation(
            ImportAutomationRequest(document={"schema": "potionui.automation"}), sample_user
        )

        assert result.success is True
        assert result.data["automation"]["id"] == "auto-1"
        assert result.data["warnings"] == warnings

    @pytest.mark.asyncio
    async def test_import_applies_name_override_without_mutating_the_request(
        self, controller, mock_manager, sample_user, sample_automation
    ):
        mock_manager.import_automation = AsyncMock(return_value=(sample_automation, []))
        document = {"schema": "potionui.automation", "automation": {"name": "Original", "graph": {}}}

        await controller.import_automation(
            ImportAutomationRequest(document=document, name="Copy"), sample_user
        )

        passed_document = mock_manager.import_automation.call_args.args[0]
        assert passed_document["automation"]["name"] == "Copy"
        # The caller's dict must not be rewritten under it.
        assert document["automation"]["name"] == "Original"

    @pytest.mark.asyncio
    async def test_import_rejects_a_foreign_document(self, controller, mock_manager, sample_user):
        mock_manager.import_automation = AsyncMock(side_effect=AutomationImportError("Not a PotionUI automation export"))

        result = await controller.import_automation(ImportAutomationRequest(document={}), sample_user)

        assert result.success is False
        assert result.error == "invalid_import"

    @pytest.mark.asyncio
    async def test_import_surfaces_structural_graph_errors(self, controller, mock_manager, sample_user):
        issues = [{"node_id": None, "message": "Graph contains a cycle", "severity": "error", "category": "structural"}]
        mock_manager.import_automation = AsyncMock(side_effect=GraphValidationError(issues))

        result = await controller.import_automation(ImportAutomationRequest(document={}), sample_user)

        assert result.success is False
        assert result.error == "invalid_graph"

    def test_list_runs_query_params_are_limit_and_before(self, built_router):
        """GET /api/automations/{id}/runs must expose `limit`/`before` keyset params."""
        run_route = next(
            r for r in built_router.routes if r.path == "/api/automations/{automation_id}/runs" and "GET" in r.methods
        )
        param_names = {p.name for p in run_route.dependant.query_params}
        assert {"limit", "before"} <= param_names


class TestAdminNodeGate:
    """The admin gate that guards graphs using `requires_admin` node types.

    Automations have no per-run user (triggers fire detached), so the controller
    refuses to author, enable, or manually run a graph that uses an admin-grade
    node unless the acting user is an administrator.
    """

    @pytest.fixture
    def registry(self):
        registry = NodeTypeRegistry()
        registry.register(NodeTypeSpec(
            key="action.backend_action", kind="action", title="Backend Action",
            outputs=(NodeField("success", "boolean"),),
            requires_admin=True, execute=AsyncMock(),
        ))
        return registry

    @pytest.fixture
    def mock_manager(self):
        manager = Mock()
        manager.create = AsyncMock()
        manager.update = AsyncMock()
        manager.set_enabled = AsyncMock()
        manager.run_now = AsyncMock()
        manager.repository = Mock()
        return manager

    @pytest.fixture
    def controller(self, mock_manager, registry):
        return AutomationController(mock_manager, registry=registry)

    @pytest.fixture
    def admin(self):
        return Mock(spec=User, id="admin-1", account_type=AccountType.ADMIN)

    @pytest.fixture
    def member(self):
        return Mock(spec=User, id="member-1", account_type=AccountType.USER)

    @staticmethod
    def _admin_graph():
        return {"nodes": [{"id": "n1", "type": "action.backend_action", "config": {}}], "edges": []}

    @pytest.mark.asyncio
    async def test_non_admin_cannot_create_graph_with_admin_node(self, controller, mock_manager, member):
        request = CreateAutomationRequest(name="danger", graph=self._admin_graph())

        result = await controller.create_automation(request, member)

        assert result.success is False
        assert result.error == "admin_required"
        mock_manager.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_admin_can_create_graph_with_admin_node(self, controller, mock_manager, admin):
        mock_manager.create.return_value = Automation(
            id="a1", name="ok", graph=self._admin_graph(), enabled=False,
        )
        request = CreateAutomationRequest(name="ok", graph=self._admin_graph())

        result = await controller.create_automation(request, admin)

        assert result.success is True
        mock_manager.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_admin_can_create_graph_without_admin_node(self, controller, mock_manager, member):
        plain_graph = {"nodes": [{"id": "n1", "type": "action.add_tag", "config": {}}], "edges": []}
        mock_manager.create.return_value = Automation(id="a1", name="ok", graph=plain_graph, enabled=False)
        request = CreateAutomationRequest(name="ok", graph=plain_graph)

        result = await controller.create_automation(request, member)

        assert result.success is True
        mock_manager.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_admin_cannot_run_admin_graph(self, controller, mock_manager, member):
        mock_manager.get.return_value = Automation(
            id="a1", name="danger", graph=self._admin_graph(), enabled=True,
        )

        result = await controller.run_now("a1", RunNowRequest(), member)

        assert result.success is False
        assert result.error == "admin_required"
        mock_manager.run_now.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_admin_cannot_enable_admin_graph(self, controller, mock_manager, member):
        mock_manager.get.return_value = Automation(
            id="a1", name="danger", graph=self._admin_graph(), enabled=False,
        )

        result = await controller.set_enabled("a1", True, member)

        assert result.success is False
        assert result.error == "admin_required"
        mock_manager.set_enabled.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_admin_can_disable_admin_graph(self, controller, mock_manager, member):
        """Disabling is always safe - it only ever reduces what can run."""
        mock_manager.get.return_value = Automation(
            id="a1", name="danger", graph=self._admin_graph(), enabled=True,
        )
        mock_manager.set_enabled.return_value = Automation(
            id="a1", name="danger", graph=self._admin_graph(), enabled=False,
        )

        result = await controller.set_enabled("a1", False, member)

        assert result.success is True
        mock_manager.set_enabled.assert_called_once()
