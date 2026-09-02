"""Controller tests: category/value reads go straight to the repository; a
single "resolve or raise" (get_category) and every mutation go through
`src.features.phrasebook.operations`, patched here exactly like the retired
manager mock (see tests/features/user_groups/test_routes.py for the
established pattern)."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.features.phrasebook import routes as routes_module
from src.features.phrasebook.dto import (
    BatchRequest,
    PhrasebookCategory,
    PhrasebookFindMode,
    PhrasebookFindScope,
    PhrasebookStateFilter,
)
from src.features.phrasebook.operations.batch import BatchError
from src.features.phrasebook.operations.core_ops import register_core_batch_operations
from src.platform.plugins.phrasebook_ops import (
    BatchOutcome,
    BatchPreview,
    PhrasebookBatchOperation,
    PhrasebookBatchOperationDefinition,
    PhrasebookOperationRegistry,
)
from src.features.phrasebook.operations.find import InvalidFields
from src.features.phrasebook.operations.matching import InvalidPattern
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)
from src.features.phrasebook.routes import PhrasebookController


@pytest.fixture
def mock_operations(monkeypatch):
    """Patch the `operations` module as seen by routes.py."""
    mock = Mock()
    monkeypatch.setattr(routes_module, "operations", mock)
    return mock


@pytest.fixture
def category_repository():
    return Mock(spec=PhrasebookCategoryRepository)


@pytest.fixture
def value_repository():
    return Mock(spec=PhrasebookValueRepository)


def hook_plugins(handler=None):
    """A plugin registry whose `execute_hook` passes the data through
    `handler(hook, data)` (default: unchanged) and records every call."""
    plugins = Mock()
    plugins.calls = []

    def execute_hook(hook, initial_data):
        data = dict(initial_data)
        if handler:
            data = handler(hook, data) or data
        plugins.calls.append((hook, data))
        return SimpleNamespace(data=data), None

    plugins.execute_hook.side_effect = execute_hook
    return plugins


@pytest.fixture
def plugins():
    return hook_plugins()


@pytest.fixture
def op_registry():
    registry = PhrasebookOperationRegistry()
    register_core_batch_operations(registry)
    return registry


@pytest.fixture
def controller(category_repository, value_repository, plugins, op_registry):
    return PhrasebookController(
        category_repository=category_repository,
        value_repository=value_repository,
        plugin_registry=plugins,
        preview_generator=Mock(),
        generation_orchestrator=Mock(),
        operation_registry=op_registry,
    )


@pytest.fixture
def user():
    return SimpleNamespace(id="user-1")


@pytest.fixture
def sample_category():
    return PhrasebookCategory(
        id="cat-1",
        name="Test",
        path="test",
        parent_id=None,
        user_id="user-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


async def test_get_categories_reads_from_repository(controller, category_repository, user, sample_category):
    category_repository.get_all.return_value = [sample_category]

    result = await controller.get_categories(user)

    assert result.success
    assert result.data["categories"][0]["id"] == "cat-1"
    category_repository.get_all.assert_called_once_with("user-1", PhrasebookStateFilter.ALL)


async def test_get_root_categories_reads_from_repository(controller, category_repository, user, sample_category):
    category_repository.get_children.return_value = [sample_category]

    result = await controller.get_categories(user, root_only=True)

    assert result.success
    category_repository.get_children.assert_called_once_with(None, "user-1")


async def test_get_category_children_reads_from_repository(controller, category_repository, user, sample_category):
    category_repository.get_children.return_value = [sample_category]

    result = await controller.get_category_children("parent-1", user)

    assert result.success
    category_repository.get_children.assert_called_once_with("parent-1", "user-1")


async def test_get_category_values_read_from_repository(
    controller, mock_operations, category_repository, value_repository, user, sample_category
):
    mock_operations.get_category.return_value = sample_category
    value_repository.get_by_category.return_value = []

    result = await controller.get_category("cat-1", user)

    assert result.success
    mock_operations.get_category.assert_called_once_with(category_repository, "cat-1", "user-1")
    value_repository.get_by_category.assert_called_once_with("cat-1", "user-1")


async def test_find_delegates_to_operation_with_every_param(
    controller, mock_operations, category_repository, value_repository, user
):
    mock_operations.parse_fields.return_value = ["value"]
    mock_operations.find_phrasebook.return_value = {
        "query": "dog", "categories": [], "values": [], "total_categories": 0, "total_values": 0,
    }

    result = await controller.find(
        user, "dog",
        mode=PhrasebookFindMode.WORD, case_sensitive=True, scope=PhrasebookFindScope.VALUES,
        include_inactive=False, path_prefix="animals", fields="value", limit=20,
    )

    assert result.success
    assert result.data["query"] == "dog"
    mock_operations.parse_fields.assert_called_once_with("value")
    mock_operations.find_phrasebook.assert_called_once_with(
        category_repository, value_repository, "user-1", "dog",
        mode="word", case_sensitive=True, scope="values", include_inactive=False,
        path_prefix="animals", fields=["value"], limit=20,
    )


async def test_find_defaults(controller, mock_operations, category_repository, value_repository, user):
    mock_operations.parse_fields.return_value = ["label", "value"]
    mock_operations.find_phrasebook.return_value = {}

    await controller.find(user, "dog")

    mock_operations.parse_fields.assert_called_once_with(None)
    mock_operations.find_phrasebook.assert_called_once_with(
        category_repository, value_repository, "user-1", "dog",
        mode="contains", case_sensitive=False, scope="all", include_inactive=True,
        path_prefix="", fields=["label", "value"], limit=200,
    )


async def test_find_invalid_regex_is_a_400(controller, mock_operations, user):
    mock_operations.parse_fields.return_value = ["label", "value"]
    mock_operations.InvalidPattern = InvalidPattern
    mock_operations.find_phrasebook.side_effect = InvalidPattern("missing ), unterminated subpattern")

    with pytest.raises(HTTPException) as excinfo:
        await controller.find(user, "(dog", mode=PhrasebookFindMode.REGEX)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail["error"] == "invalid_pattern"
    assert "unterminated" in excinfo.value.detail["message"]


async def test_find_invalid_fields_is_a_400(controller, mock_operations, user):
    mock_operations.InvalidFields = InvalidFields
    mock_operations.parse_fields.side_effect = InvalidFields("Unknown fields: description")

    with pytest.raises(HTTPException) as excinfo:
        await controller.find(user, "dog", fields="description")

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail["error"] == "invalid_fields"
    mock_operations.find_phrasebook.assert_not_called()


async def test_find_reports_failure(controller, mock_operations, user):
    mock_operations.parse_fields.return_value = ["label", "value"]
    mock_operations.InvalidPattern = InvalidPattern
    mock_operations.find_phrasebook.side_effect = RuntimeError("boom")

    result = await controller.find(user, "dog")

    assert not result.success
    assert result.error == "find_failed"


async def test_find_results_hook_can_rewrite_hits(
    category_repository, value_repository, op_registry, mock_operations, user
):
    seen = {}

    def handler(hook, data):
        seen.update(data)
        data["values"] = [{"id": "injected"}]
        return data

    controller = PhrasebookController(
        category_repository=category_repository, value_repository=value_repository,
        plugin_registry=hook_plugins(handler), preview_generator=Mock(),
        generation_orchestrator=Mock(), operation_registry=op_registry,
    )
    mock_operations.parse_fields.return_value = ["label", "value"]
    mock_operations.find_phrasebook.return_value = {
        "query": "dog", "mode": "contains", "case_sensitive": False, "scope": "all",
        "categories": [{"id": "c"}], "values": [{"id": "v"}], "total_categories": 1, "total_values": 1,
    }

    result = await controller.find(user, "dog", path_prefix="animals")

    assert result.data["values"] == [{"id": "injected"}]
    assert result.data["categories"] == [{"id": "c"}]
    assert seen["user_id"] == "user-1"
    assert seen["path_prefix"] == "animals"
    assert seen["fields"] == ["label", "value"]
    assert seen["limit"] == 200


# ---- batch ----

class RecordingOp(PhrasebookBatchOperation):
    supports_preview = True

    def __init__(self):
        self.runs = []
        self.previews = []

    async def preview(self, ctx, value_ids, params):
        self.previews.append((ctx.user_id, value_ids, params))
        return BatchPreview(items=[{"id": value_ids[0], "field": "label", "before": "a", "after": "b"}], changed=1)

    async def run(self, ctx, value_ids, params):
        self.runs.append((ctx.user_id, value_ids, params))
        return BatchOutcome(updated=[{"id": i} for i in value_ids], message="shouted")


def batch(op, value_ids, **params):
    return BatchRequest(op=op, value_ids=value_ids, params=params)


@pytest.fixture
def plugin_op(op_registry):
    op = RecordingOp()
    op_registry.register(PhrasebookBatchOperationDefinition(
        op_id="shout", label="Shout", backend=op,
        frontend_component="plugin:shouter:ShoutModal.svelte", source="shouter",
    ))
    return op


async def test_batch_dispatches_a_plugin_op_through_the_registry(controller, plugins, plugin_op, user):
    result = await controller.batch_values(batch("shout", ["v1", "v2", "v1"], volume=11), user)

    assert result.success
    assert result.data == {"updated": [{"id": "v1"}, {"id": "v2"}], "skipped": [], "deleted": [], "message": "shouted"}
    assert plugin_op.runs == [("user-1", ["v1", "v2"], {"volume": 11})]
    hooks = [h for h, _ in plugins.calls]
    assert hooks == ["phrasebook.batch.before", "phrasebook.batch.after"]
    before, after = (d for _, d in plugins.calls)
    assert before == {"op": "shout", "value_ids": ["v1", "v2"], "params": {"volume": 11}, "user_id": "user-1"}
    assert after["outcome"]["message"] == "shouted"
    assert after["value_ids"] == ["v1", "v2"]


async def test_batch_unknown_op_is_a_404(controller, plugins, user):
    with pytest.raises(HTTPException) as excinfo:
        await controller.batch_values(batch("teleport", ["v1"]), user)

    assert excinfo.value.status_code == 404
    assert excinfo.value.detail["error"] == "unknown_op"
    assert plugins.calls == []


async def test_batch_empty_selection_is_a_400(controller, user):
    with pytest.raises(HTTPException) as excinfo:
        await controller.batch_values(batch("replace", [], find="x"), user)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail["error"] == "empty_selection"


async def test_before_hook_veto_blocks_the_run(category_repository, value_repository, op_registry, user):
    plugin_op = RecordingOp()
    op_registry.register(PhrasebookBatchOperationDefinition(op_id="shout", label="Shout", backend=plugin_op, source="shouter"))

    def veto(hook, data):
        if hook == "phrasebook.batch.before":
            data["blocked"] = True
            data["block_reason"] = "Quiet hours"
        return data

    controller = PhrasebookController(
        category_repository=category_repository, value_repository=value_repository,
        plugin_registry=hook_plugins(veto), preview_generator=Mock(),
        generation_orchestrator=Mock(), operation_registry=op_registry,
    )

    with pytest.raises(HTTPException) as excinfo:
        await controller.batch_values(batch("shout", ["v1"]), user)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == {"success": False, "error": "blocked", "message": "Quiet hours", "code": None}
    assert plugin_op.runs == []


async def test_before_hook_can_rewrite_params_and_ids(category_repository, value_repository, op_registry, user):
    plugin_op = RecordingOp()
    op_registry.register(PhrasebookBatchOperationDefinition(op_id="shout", label="Shout", backend=plugin_op, source="shouter"))

    def rewrite(hook, data):
        if hook == "phrasebook.batch.before":
            data["params"] = {**data["params"], "volume": 3}
            data["value_ids"] = [i for i in data["value_ids"] if i != "locked"]
        return data

    plugins = hook_plugins(rewrite)
    controller = PhrasebookController(
        category_repository=category_repository, value_repository=value_repository,
        plugin_registry=plugins, preview_generator=Mock(),
        generation_orchestrator=Mock(), operation_registry=op_registry,
    )

    await controller.batch_values(batch("shout", ["v1", "locked"], volume=11), user)

    assert plugin_op.runs == [("user-1", ["v1"], {"volume": 3})]
    after = plugins.calls[-1][1]
    assert after["params"] == {"volume": 3}
    assert after["value_ids"] == ["v1"]


async def test_preview_dispatches_without_hooks(controller, plugins, plugin_op, user):
    result = await controller.preview_batch(batch("shout", ["v1"], volume=2), user)

    assert result.success
    assert result.data == {
        "items": [{"id": "v1", "field": "label", "before": "a", "after": "b"}], "changed": 1, "unchanged": [],
    }
    assert plugin_op.previews == [("user-1", ["v1"], {"volume": 2})]
    assert plugins.calls == []


async def test_preview_on_an_op_without_preview_is_a_400(controller, user):
    with pytest.raises(HTTPException) as excinfo:
        await controller.preview_batch(batch("delete", ["v1"]), user)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail["error"] == "no_preview"


async def test_core_replace_is_routed_through_the_registry(controller, plugins, op_registry, value_repository, user, monkeypatch):
    seen = {}

    async def fake_run(ctx, value_ids, params):
        seen.update(user_id=ctx.user_id, value_ids=value_ids, params=params, ctx=ctx)
        return BatchOutcome(updated=[{"id": "v1"}], skipped=[], message="Replaced in 1 value")

    monkeypatch.setattr(op_registry.get("replace").backend, "run", fake_run)

    result = await controller.batch_values(batch("replace", ["v1"], find="dog", replace="cat"), user)

    assert result.data["message"] == "Replaced in 1 value"
    assert seen["value_ids"] == ["v1"]
    assert seen["params"] == {"find": "dog", "replace": "cat"}
    assert isinstance(seen["ctx"], routes_module.operations.RepositoryBatchContext)
    assert seen["ctx"].plugins is plugins
    assert op_registry.get("replace").source == "core"


async def test_batch_operation_error_maps_to_its_code_and_status(controller, op_registry, user, monkeypatch):
    async def failing(ctx, value_ids, params):
        raise BatchError("unknown_values", "Unknown values: v9")

    monkeypatch.setattr(op_registry.get("delete").backend, "run", failing)

    with pytest.raises(HTTPException) as excinfo:
        await controller.batch_values(batch("delete", ["v9"]), user)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail["error"] == "unknown_values"
    assert excinfo.value.detail["message"] == "Unknown values: v9"


async def test_batch_unexpected_failure(controller, plugins, op_registry, user, monkeypatch):
    async def boom(ctx, value_ids, params):
        raise RuntimeError("boom")

    monkeypatch.setattr(op_registry.get("set_active").backend, "run", boom)

    result = await controller.batch_values(batch("set_active", ["v1"], is_active=True), user)

    assert not result.success
    assert result.error == "batch_failed"
    assert [h for h, _ in plugins.calls] == ["phrasebook.batch.before"]


def test_list_batch_ops_manifest(controller, plugin_op):
    result = controller.list_batch_ops()

    by_id = {entry["id"]: entry for entry in result.data}
    assert set(by_id) == {"replace", "set_active", "move", "delete", "shout"}
    assert by_id["replace"] == {"id": "replace", "label": "Replace…", "component": None, "has_preview": True, "source": "core"}
    assert by_id["shout"]["component"] == "plugin:shouter:ShoutModal.svelte"
    assert by_id["shout"]["source"] == "shouter"


def test_batch_request_shape():
    request = BatchRequest.model_validate({"op": "move", "value_ids": ["a"], "params": {"category_id": "c"}})
    assert request.params["category_id"] == "c"
    assert BatchRequest.model_validate({"op": "delete", "value_ids": ["a"]}).params == {}
    with pytest.raises(ValidationError):
        BatchRequest.model_validate({"op": "delete"})
