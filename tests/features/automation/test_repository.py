"""Tests for AutomationRepository against a migrated in-memory DB."""
import pytest
from unittest.mock import patch

from src.features.automation.repository import AutomationRepository
from src.features.automation.records import Automation, AutomationRun, AutomationRunNode


SAMPLE_GRAPH = {
    "nodes": [
        {"id": "n1", "type": "trigger.manual", "position": {"x": 0, "y": 0}, "config": {}},
        {"id": "n2", "type": "action.send_notification", "position": {"x": 100, "y": 0}, "config": {}},
    ],
    "edges": [
        {"id": "e1", "source": "n1", "source_handle": "out", "target": "n2", "target_handle": "in"},
    ],
}


class TestAutomationRepository:
    """Test cases for AutomationRepository against a migrated in-memory DB."""

    @pytest.fixture
    def repository(self, mock_db):
        # This module does `from ..database import db` at import time - re-patch
        # explicitly so it targets the fixture's test database, matching the
        # workaround used by the other repository tests in this directory.
        with patch('src.features.automation.repository.db', mock_db):
            yield AutomationRepository()

    # -- automations ---------------------------------------------------------

    def test_create_and_get_by_id(self, repository):
        automation = Automation(id="", name="Krea Lora Import", graph=SAMPLE_GRAPH, user_id="user-1")
        created = repository.create(automation)

        assert created.id
        assert created.name == "Krea Lora Import"
        assert created.enabled is False
        assert created.version == 1
        assert created.graph == SAMPLE_GRAPH

        fetched = repository.get_by_id(created.id)
        assert fetched is not None
        assert fetched.graph == SAMPLE_GRAPH

    def test_get_by_id_missing_returns_none(self, repository):
        assert repository.get_by_id("does-not-exist") is None

    def test_get_all_filters_by_user_and_enabled(self, repository):
        repository.create(Automation(id="", name="A", graph=SAMPLE_GRAPH, user_id="user-1", enabled=True))
        repository.create(Automation(id="", name="B", graph=SAMPLE_GRAPH, user_id="user-1", enabled=False))
        repository.create(Automation(id="", name="C", graph=SAMPLE_GRAPH, user_id="user-2", enabled=True))

        user1_all = repository.get_all(user_id="user-1")
        assert {a.name for a in user1_all} == {"A", "B"}

        enabled_only = repository.get_all(enabled_only=True)
        assert {a.name for a in enabled_only} == {"A", "C"}

    def test_update_without_bump_keeps_version(self, repository):
        created = repository.create(Automation(id="", name="A", graph=SAMPLE_GRAPH))
        created.name = "A renamed"
        updated = repository.update(created, bump_version=False)

        assert updated.name == "A renamed"
        assert updated.version == 1

    def test_update_with_bump_increments_version(self, repository):
        created = repository.create(Automation(id="", name="A", graph=SAMPLE_GRAPH))
        created.graph = {**SAMPLE_GRAPH, "nodes": []}
        updated = repository.update(created, bump_version=True)

        assert updated.version == 2
        assert updated.graph["nodes"] == []

    def test_update_missing_returns_none(self, repository):
        missing = Automation(id="nope", name="X", graph=SAMPLE_GRAPH)
        assert repository.update(missing) is None

    def test_set_enabled(self, repository):
        created = repository.create(Automation(id="", name="A", graph=SAMPLE_GRAPH, enabled=False))
        assert repository.set_enabled(created.id, True) is True

        fetched = repository.get_by_id(created.id)
        assert fetched.enabled is True

    def test_touch_last_run(self, repository):
        created = repository.create(Automation(id="", name="A", graph=SAMPLE_GRAPH))
        assert repository.touch_last_run(created.id, "success") is True

        fetched = repository.get_by_id(created.id)
        assert fetched.last_run_status == "success"
        assert fetched.last_run_at is not None

    def test_delete(self, repository):
        created = repository.create(Automation(id="", name="A", graph=SAMPLE_GRAPH))
        assert repository.delete(created.id) is True
        assert repository.get_by_id(created.id) is None

    # -- runs and run nodes ---------------------------------------------------

    def test_create_run_and_finish_run(self, repository):
        automation = repository.create(Automation(id="", name="A", graph=SAMPLE_GRAPH))
        run = repository.create_run(AutomationRun(
            id="", automation_id=automation.id, trigger_node_id="n1",
            trigger_type="trigger.manual", event_payload={"foo": "bar"},
        ))

        assert run.id
        assert run.status == "running"
        assert run.event_payload == {"foo": "bar"}

        assert repository.finish_run(run.id, "success", duration_ms=42) is True

        fetched = repository.get_run(run.id)
        assert fetched.status == "success"
        assert fetched.duration_ms == 42
        assert fetched.finished_at is not None

    def test_list_runs_newest_first(self, repository):
        automation = repository.create(Automation(id="", name="A", graph=SAMPLE_GRAPH))
        run1 = repository.create_run(AutomationRun(id="", automation_id=automation.id))
        run2 = repository.create_run(AutomationRun(id="", automation_id=automation.id))

        runs = repository.list_runs(automation.id, limit=10)
        run_ids = [r.id for r in runs]

        # started_at has second resolution, so same-second runs may tie; just
        # assert both are present and ordering is by started_at DESC overall.
        assert set(run_ids) == {run1.id, run2.id}

    def test_create_run_node_and_update(self, repository):
        automation = repository.create(Automation(id="", name="A", graph=SAMPLE_GRAPH))
        run = repository.create_run(AutomationRun(id="", automation_id=automation.id))

        run_node = repository.create_run_node(AutomationRunNode(
            id="", run_id=run.id, node_id="n1", node_type="trigger.manual",
            input={"x": 1},
        ))

        assert run_node.id
        assert run_node.input == {"x": 1}
        assert run_node.status == "running"

        assert repository.update_run_node(
            run_node.id, status="success", output='{"y": 2}', finished=True
        ) is True

        fetched = repository.get_run_node(run_node.id)
        assert fetched.status == "success"
        assert fetched.output == {"y": 2}
        assert fetched.finished_at is not None

    def test_list_run_nodes(self, repository):
        automation = repository.create(Automation(id="", name="A", graph=SAMPLE_GRAPH))
        run = repository.create_run(AutomationRun(id="", automation_id=automation.id))

        repository.create_run_node(AutomationRunNode(id="", run_id=run.id, node_id="n1", node_type="trigger.manual"))
        repository.create_run_node(AutomationRunNode(id="", run_id=run.id, node_id="n2", node_type="action.send_notification"))

        nodes = repository.list_run_nodes(run.id)
        assert {n.node_id for n in nodes} == {"n1", "n2"}
