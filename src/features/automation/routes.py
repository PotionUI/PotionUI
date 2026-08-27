"""
Automation Controller

REST endpoints for the automation module: CRUD on automations, manual runs,
graph validation, the node-type palette catalog, and run history. Business
logic lives in `AutomationRuntime`; this is a thin route layer.
"""
import logging
import uuid
from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.features.automation.dto import (
    CreateAutomationRequest,
    ImportAutomationRequest,
    InstantiateAutomationTemplateRequest,
    RunNowRequest,
    UpdateAutomationRequest,
    ValidateGraphRequest,
)
from src.features.automation.runtime import (
    AutomationImportError,
    AutomationRuntime,
    AutomationTemplateNotFoundError,
    AutomationTemplateUnavailableError,
    GraphValidationError,
)
from src.platform.plugins.automation_nodes import NodeTypeRegistry, node_type_registry, resolved_config_schema
from src.platform.security.user import AccountType, User
from src.platform.websocket.automation_connection_hub import automation_connection_hub

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


def _serialize_config_schema(config_schema: list) -> dict:
    """
    Convert the internal `config_schema` field-def list (each with a `name`
    key - see `src.platform.plugins.automation_nodes.NodeTypeSpec`) into the flat
    `{"properties": {field_name: field_config}}` shape the frontend
    `FormField` render loop consumes.
    """
    properties = {}
    for field_def in config_schema:
        name = field_def.get("name")
        if not name:
            continue
        properties[name] = {k: v for k, v in field_def.items() if k != "name"}
    return {"properties": properties}


def _serialize_node_type(spec) -> dict:
    result = {
        "key": spec.key,
        "kind": spec.kind,
        "title": spec.title,
        "description": spec.description,
        "icon": spec.icon,
        "category": spec.category,
        # `resolved_config_schema` calls any field's `options_provider` (e.g.
        # the filesystem trigger's app-directory picker, the hook-event
        # trigger's live hooks catalog) and inlines the result - the raw
        # config_schema is never serialized directly so a callable can never
        # leak into the JSON response.
        "config_schema": _serialize_config_schema(resolved_config_schema(spec)),
        "output_ports": list(spec.output_ports),
        # The DATA contract (what downstream nodes can read), as opposed to
        # `output_ports`, which are the control-flow edge handles. For triggers
        # these describe the fired event payload, i.e. `event.*`.
        "outputs": [
            {
                "key": f.key,
                "type": f.type,
                "label": f.label,
                "description": f.description,
                "example": f.example,
            }
            for f in spec.outputs
        ],
    }
    if spec.item_outputs:
        # Fan-out node types only (`NodeResult.items` set - see
        # `src.platform.plugins.automation_nodes.NodeResult`): the shape of
        # ONE item, distinct from `outputs` above (this node's own aggregate
        # result). `upstream.<node_id>.*` resolves against this list inside
        # the node's downstream subtree.
        result["item_outputs"] = [
            {
                "key": f.key,
                "type": f.type,
                "label": f.label,
                "description": f.description,
                "example": f.example,
            }
            for f in spec.item_outputs
        ]
    if spec.requires_admin:
        # Admin-grade node - the palette can hide/disable it for non-admins, and
        # the controller refuses to author, enable, or run a graph that uses it.
        result["requires_admin"] = True
    if spec.dynamic_outputs:
        # `outputs` is empty *because the shape isn't knowable*, not because the
        # node emits nothing - the canvas renders "runtime-defined" instead.
        result["dynamic_outputs"] = True
    if spec.dynamic_ports_config_key:
        # Signals to the canvas editor that `output_ports` above is just the
        # static fallback - real ports for a given node instance come from
        # splitting that instance's own `config[dynamic_ports_config_key]`
        # value (comma-separated) plus an implicit trailing "default" port.
        result["dynamic_ports_config_key"] = spec.dynamic_ports_config_key
    return result


class AutomationController(BaseController):
    """Controller for automation CRUD, execution, and run-history operations."""

    def __init__(self, automation_runtime: AutomationRuntime, registry: NodeTypeRegistry = node_type_registry):
        super().__init__()
        self.manager = automation_runtime
        self.registry = registry

    # -- authorization -------------------------------------------------------

    def _admin_denial(self, graph: Optional[dict], user: User) -> Optional[APIResponse]:
        """Deny non-admins any graph that uses an admin-grade node type.

        Node execution has no per-run user (triggers fire detached), so the gate
        sits wherever a graph enters the system or becomes runnable: create,
        update, import, template instantiation, enabling, and manual run. Admins
        are trusted; a graph an admin authored may then be fired by a trigger.
        Returns a 403 APIResponse to short-circuit, or None to proceed.
        """
        if user.account_type == AccountType.ADMIN:
            return None
        admin_types = {spec.key for spec in self.registry.all() if spec.requires_admin}
        nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
        used = {node.get("type") for node in nodes if isinstance(node, dict)}
        forbidden = sorted(t for t in used & admin_types if t)
        if not forbidden:
            return None
        return self.error_api_response(
            error="admin_required",
            message=(
                "These node types require administrator privileges: "
                + ", ".join(forbidden)
            ),
        )

    # -- CRUD ----------------------------------------------------------------

    async def list_automations(self, user: User) -> APIResponse:
        try:
            automations = self.manager.list()
            return self.success_response(data=[a.to_dict() for a in automations])
        except Exception as e:
            self.logger.error(f"Error listing automations: {e}")
            return self.error_api_response(error="list_automations_failed", message=str(e))

    async def create_automation(self, request: CreateAutomationRequest, user: User) -> APIResponse:
        denial = self._admin_denial(request.graph, user)
        if denial:
            return denial
        try:
            automation = await self.manager.create(
                name=request.name,
                graph=request.graph,
                description=request.description,
                user_id=user.id,
                enabled=request.enabled,
            )
            return self.success_response(data=automation.to_dict())
        except GraphValidationError as e:
            return self.error_api_response(error="invalid_graph", message=str(e.issues))
        except Exception as e:
            self.logger.error(f"Error creating automation: {e}")
            return self.error_api_response(error="create_automation_failed", message=str(e))

    async def get_automation(self, automation_id: str, user: User) -> APIResponse:
        try:
            automation = self.manager.get(automation_id)
            if automation is None:
                return self.error_api_response(error="automation_not_found", message="Automation not found")
            return self.success_response(data=automation.to_dict())
        except Exception as e:
            self.logger.error(f"Error getting automation: {e}")
            return self.error_api_response(error="get_automation_failed", message=str(e))

    # -- portability ---------------------------------------------------------

    async def list_templates(self, user: User) -> APIResponse:
        try:
            return self.success_response(data=self.manager.list_templates())
        except Exception as e:
            self.logger.error(f"Error listing automation templates: {e}")
            return self.error_api_response(error="list_templates_failed", message=str(e))

    async def instantiate_template(
        self,
        template_key: str,
        request: InstantiateAutomationTemplateRequest,
        user: User,
    ) -> APIResponse:
        try:
            automation, warnings = await self.manager.instantiate_template(
                template_key,
                user_id=user.id,
                name=request.name,
            )
            return self.success_response(data={"automation": automation.to_dict(), "warnings": warnings})
        except AutomationTemplateNotFoundError:
            return self.error_api_response(
                error="template_not_found",
                message=f"Automation template not found: {template_key}",
            )
        except AutomationTemplateUnavailableError as e:
            return self.error_api_response(error="template_unavailable", message=str(e))
        except AutomationImportError as e:
            return self.error_api_response(error="invalid_template", message=str(e))
        except GraphValidationError as e:
            return self.error_api_response(error="invalid_template_graph", message=str(e.issues))
        except Exception as e:
            self.logger.error(f"Error instantiating automation template {template_key}: {e}")
            return self.error_api_response(error="instantiate_template_failed", message=str(e))

    async def export_automation(self, automation_id: str, user: User) -> APIResponse:
        try:
            envelope = self.manager.export_automation(automation_id)
            if envelope is None:
                return self.error_api_response(error="automation_not_found", message="Automation not found")
            return self.success_response(data=envelope)
        except Exception as e:
            self.logger.error(f"Error exporting automation: {e}")
            return self.error_api_response(error="export_automation_failed", message=str(e))

    async def import_automation(self, request: ImportAutomationRequest, user: User) -> APIResponse:
        """Imported automations always land disabled; `warnings` lists what this machine can't satisfy yet."""
        try:
            document = dict(request.document)
            if request.name:
                automation_payload = dict(document.get("automation") or {})
                automation_payload["name"] = request.name
                document["automation"] = automation_payload

            import_graph = (document.get("automation") or {}).get("graph")
            denial = self._admin_denial(import_graph, user)
            if denial:
                return denial

            automation, warnings = await self.manager.import_automation(document, user_id=user.id)
            return self.success_response(data={"automation": automation.to_dict(), "warnings": warnings})
        except AutomationImportError as e:
            return self.error_api_response(error="invalid_import", message=str(e))
        except GraphValidationError as e:
            return self.error_api_response(error="invalid_graph", message=str(e.issues))
        except Exception as e:
            self.logger.error(f"Error importing automation: {e}")
            return self.error_api_response(error="import_automation_failed", message=str(e))

    async def update_automation(self, automation_id: str, request: UpdateAutomationRequest, user: User) -> APIResponse:
        # A partial update omits `graph`; only inspect one that's actually supplied.
        if request.graph is not None:
            denial = self._admin_denial(request.graph, user)
            if denial:
                return denial
        try:
            automation = await self.manager.update(
                automation_id,
                name=request.name,
                graph=request.graph,
                description=request.description,
                enabled=request.enabled,
            )
            if automation is None:
                return self.error_api_response(error="automation_not_found", message="Automation not found")
            return self.success_response(data=automation.to_dict())
        except GraphValidationError as e:
            return self.error_api_response(error="invalid_graph", message=str(e.issues))
        except Exception as e:
            self.logger.error(f"Error updating automation: {e}")
            return self.error_api_response(error="update_automation_failed", message=str(e))

    async def set_enabled(self, automation_id: str, enabled: bool, user: User) -> APIResponse:
        # Enabling arms triggers, which fire with no user - so a non-admin must
        # not enable a graph that uses admin-grade nodes. Disabling is always fine.
        if enabled:
            existing = self.manager.get(automation_id)
            if existing is not None:
                denial = self._admin_denial(existing.graph, user)
                if denial:
                    return denial
        try:
            automation = await self.manager.set_enabled(automation_id, enabled)
            if automation is None:
                return self.error_api_response(error="automation_not_found", message="Automation not found")
            return self.success_response(data=automation.to_dict())
        except Exception as e:
            self.logger.error(f"Error setting automation enabled state: {e}")
            return self.error_api_response(error="set_enabled_failed", message=str(e))

    async def delete_automation(self, automation_id: str, user: User) -> APIResponse:
        try:
            success = await self.manager.delete(automation_id)
            if not success:
                return self.error_api_response(error="automation_not_found", message="Automation not found")
            return self.success_response(data={"id": automation_id})
        except Exception as e:
            self.logger.error(f"Error deleting automation: {e}")
            return self.error_api_response(error="delete_automation_failed", message=str(e))

    # -- execution / validation ------------------------------------------------

    async def run_now(self, automation_id: str, request: RunNowRequest, user: User) -> APIResponse:
        # There is no automation ownership model - any authenticated user can run
        # any automation by id - so a manual run of an admin-node graph must be
        # admin-gated here too, not only at authoring time.
        existing = self.manager.get(automation_id)
        if existing is not None:
            denial = self._admin_denial(existing.graph, user)
            if denial:
                return denial
        try:
            run_id = await self.manager.run_now(automation_id, node_id=request.node_id, payload=request.payload)
            if run_id is None:
                return self.error_api_response(
                    error="run_failed", message="Automation not found or has no trigger node"
                )
            return self.success_response(data={"run_id": run_id})
        except Exception as e:
            self.logger.error(f"Error running automation: {e}")
            return self.error_api_response(error="run_failed", message=str(e))

    async def validate_graph(self, request: ValidateGraphRequest, user: User) -> APIResponse:
        try:
            issues = self.manager.validate_graph(request.graph)
            return self.success_response(data={"issues": issues})
        except Exception as e:
            self.logger.error(f"Error validating automation graph: {e}")
            return self.error_api_response(error="validate_graph_failed", message=str(e))

    async def get_node_types(self, user: User) -> APIResponse:
        try:
            node_types = [_serialize_node_type(spec) for spec in self.registry.all()]
            return self.success_response(data=node_types)
        except Exception as e:
            self.logger.error(f"Error listing node types: {e}")
            return self.error_api_response(error="list_node_types_failed", message=str(e))

    # -- run history -----------------------------------------------------------

    async def list_runs(self, automation_id: str, user: User, limit: int = 20, before: Optional[str] = None) -> APIResponse:
        try:
            runs = self.manager.repository.list_runs(automation_id, limit=limit, before=before)
            return self.success_response(data=[r.to_dict() for r in runs])
        except Exception as e:
            self.logger.error(f"Error listing automation runs: {e}")
            return self.error_api_response(error="list_runs_failed", message=str(e))

    async def get_run(self, automation_id: str, run_id: str, user: User) -> APIResponse:
        try:
            run = self.manager.repository.get_run(run_id)
            if run is None or run.automation_id != automation_id:
                return self.error_api_response(error="run_not_found", message="Run not found")
            run_nodes = self.manager.repository.list_run_nodes(run_id)
            data = run.to_dict()
            data["nodes"] = [n.to_dict() for n in run_nodes]
            return self.success_response(data=data)
        except Exception as e:
            self.logger.error(f"Error getting automation run: {e}")
            return self.error_api_response(error="get_run_failed", message=str(e))


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.automation_controller
    # Automations is an admin-area feature (Admin -> Automations); there is no
    # non-admin surface for it and no per-automation ownership model, so the
    # whole HTTP router is admin-gated rather than left on any authenticated
    # user. The /ws/automations socket keeps its own token auth.
    router = APIRouter(
        prefix="/api/automations",
        tags=["Automations"],
        dependencies=[Depends(get_current_admin_user)],
    )

    # Route ordering gotcha: static catalog/import routes must be declared
    # before the `/{automation_id}` family or FastAPI will swallow them as path params.

    @router.get("/templates", response_model=APIResponse, summary="List Automation Templates")
    async def list_templates(current_user: User = Depends(get_current_active_user)) -> APIResponse:
        """List immutable core and enabled-plugin templates with requirement status."""
        return await controller.list_templates(current_user)

    @router.post(
        "/templates/{template_key}/instantiate",
        response_model=APIResponse,
        summary="Instantiate Automation Template",
    )
    async def instantiate_template(
        template_key: str,
        request: InstantiateAutomationTemplateRequest,
        current_user: User = Depends(get_current_active_user),
    ) -> APIResponse:
        """Create a fresh, always-disabled automation from a catalog template."""
        return await controller.instantiate_template(template_key, request, current_user)

    @router.get("/node-types", response_model=APIResponse, summary="List Automation Node Types")
    async def get_node_types(current_user: User = Depends(get_current_active_user)) -> APIResponse:
        """Palette catalog of every registered trigger/condition/action node type."""
        return await controller.get_node_types(current_user)

    @router.post("/import", response_model=APIResponse, summary="Import Automation")
    async def import_automation(
        request: ImportAutomationRequest,
        current_user: User = Depends(get_current_active_user),
    ) -> APIResponse:
        """Create a new (always disabled) automation from an exported envelope."""
        return await controller.import_automation(request, current_user)

    @router.post("/validate", response_model=APIResponse, summary="Validate Automation Graph")
    async def validate_graph(
        request: ValidateGraphRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Validate a graph without persisting it (unknown types, cycles, dangling edges, missing config)."""
        return await controller.validate_graph(request, current_user)

    @router.get("/", response_model=APIResponse, summary="List Automations")
    async def list_automations(current_user: User = Depends(get_current_active_user)) -> APIResponse:
        return await controller.list_automations(current_user)

    @router.post("/", response_model=APIResponse, summary="Create Automation")
    async def create_automation(
        request: CreateAutomationRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.create_automation(request, current_user)

    @router.get("/{automation_id}", response_model=APIResponse, summary="Get Automation")
    async def get_automation(
        automation_id: str,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.get_automation(automation_id, current_user)

    @router.get("/{automation_id}/export", response_model=APIResponse, summary="Export Automation")
    async def export_automation(
        automation_id: str,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Portable envelope for one automation - no ids, no enabled state, no timestamps."""
        return await controller.export_automation(automation_id, current_user)

    @router.put("/{automation_id}", response_model=APIResponse, summary="Update Automation")
    async def update_automation(
        automation_id: str,
        request: UpdateAutomationRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.update_automation(automation_id, request, current_user)

    @router.patch("/{automation_id}/enable", response_model=APIResponse, summary="Enable Automation")
    async def enable_automation(
        automation_id: str,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.set_enabled(automation_id, True, current_user)

    @router.patch("/{automation_id}/disable", response_model=APIResponse, summary="Disable Automation")
    async def disable_automation(
        automation_id: str,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.set_enabled(automation_id, False, current_user)

    @router.delete("/{automation_id}", response_model=APIResponse, summary="Delete Automation")
    async def delete_automation(
        automation_id: str,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.delete_automation(automation_id, current_user)

    @router.post("/{automation_id}/run", response_model=APIResponse, summary="Run Automation Now")
    async def run_now(
        automation_id: str,
        request: RunNowRequest = RunNowRequest(),
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.run_now(automation_id, request, current_user)

    @router.get("/{automation_id}/runs", response_model=APIResponse, summary="List Automation Runs")
    async def list_runs(
        automation_id: str,
        limit: int = Query(20, description="Max results"),
        before: Optional[str] = Query(None, description="Keyset cursor: id of the oldest already-seen run"),
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.list_runs(automation_id, current_user, limit=limit, before=before)

    @router.get("/{automation_id}/runs/{run_id}", response_model=APIResponse, summary="Get Automation Run")
    async def get_run(
        automation_id: str,
        run_id: str,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.get_run(automation_id, run_id, current_user)

    return router


def build_ws_router(container: "AppContainer") -> APIRouter:
    ws_router = APIRouter(tags=["WebSocket"])

    @ws_router.websocket("/ws/automations")
    async def websocket_automations_endpoint(websocket: WebSocket, token: str = Query(None)):
        """WebSocket endpoint for real-time automation run status (broadcast to all authenticated clients)."""
        from src.platform.security.current_user import authenticate_websocket_token

        try:
            user, auth_error = authenticate_websocket_token(token)
        except Exception as e:
            logging.error(f"Automation WebSocket auth exception: {e}")
            try:
                await websocket.accept()
                await websocket.close(code=4001, reason="Authentication error")
            except Exception as close_error:
                logging.error(f"Failed to close automation WebSocket after auth error: {close_error}")
            return

        if user is None:
            logging.warning(f"Automation WebSocket auth failed: {auth_error}")
            try:
                await websocket.accept()
                await websocket.close(code=4001, reason=auth_error or "Authentication failed")
            except Exception as e:
                logging.error(f"Error closing automation WebSocket after auth failure: {e}")
            return

        client_id = str(uuid.uuid4())
        await automation_connection_hub.connect(websocket, client_id)

        try:
            await websocket.send_json({"type": "connection_established", "client_id": client_id})

            while True:
                data = await websocket.receive_json()
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logging.error(f"Automation WebSocket handler error for client {client_id}: {e}")
        finally:
            automation_connection_hub.disconnect(client_id)

    return ws_router
