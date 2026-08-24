"""Tests for the Automation/AutomationRun/AutomationRunNode dataclasses."""

import unittest
from datetime import datetime

from src.features.automation.records import Automation, AutomationRun, AutomationRunNode


class _FakeRow(dict):
    """Minimal stand-in for a sqlite3.Row (supports `row['col']` access)."""
    def __getitem__(self, key):
        return dict.get(self, key)


class TestAutomationModel(unittest.TestCase):

    def test_serialize_and_from_row_roundtrip(self):
        graph = {"nodes": [{"id": "n1", "type": "trigger.manual"}], "edges": []}
        automation = Automation(id="a1", name="Test", graph=graph, description="desc", enabled=True, version=2)

        row = _FakeRow(
            id="a1", name="Test", description="desc", enabled=1,
            graph=automation.serialize_graph(), version=2, user_id=None,
            created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
            last_run_at=None, last_run_status=None,
        )
        restored = Automation.from_row(row)

        self.assertEqual(restored.graph, graph)
        self.assertTrue(restored.enabled)
        self.assertEqual(restored.version, 2)
        self.assertIsInstance(restored.created_at, datetime)

    def test_to_dict_serializes_dates(self):
        automation = Automation(id="a1", name="Test", graph={}, created_at=datetime(2026, 1, 1))
        d = automation.to_dict()

        self.assertEqual(d["id"], "a1")
        self.assertEqual(d["created_at"], "2026-01-01T00:00:00")


class TestAutomationRunModel(unittest.TestCase):

    def test_serialize_event_payload_roundtrip(self):
        run = AutomationRun(id="r1", automation_id="a1", event_payload={"path": "/x"})
        row = _FakeRow(
            id="r1", automation_id="a1", trigger_node_id=None, trigger_type=None,
            status="running", event_payload=run.serialize_event_payload(), error=None,
            started_at="2026-01-01T00:00:00", finished_at=None, duration_ms=None,
        )
        restored = AutomationRun.from_row(row)

        self.assertEqual(restored.event_payload, {"path": "/x"})

    def test_none_event_payload_serializes_to_none(self):
        run = AutomationRun(id="r1", automation_id="a1")
        self.assertIsNone(run.serialize_event_payload())


class TestAutomationRunNodeModel(unittest.TestCase):

    def test_input_output_roundtrip(self):
        node = AutomationRunNode(id="n1", run_id="r1", node_id="node1", node_type="action.add_tag",
                                  input={"x": 1}, output={"y": 2})
        row = _FakeRow(
            id="n1", run_id="r1", node_id="node1", node_type="action.add_tag", status="success",
            input=node.serialize_input(), output=node.serialize_output(), error=None,
            started_at="2026-01-01T00:00:00", finished_at="2026-01-01T00:00:01", duration_ms=1000,
        )
        restored = AutomationRunNode.from_row(row)

        self.assertEqual(restored.input, {"x": 1})
        self.assertEqual(restored.output, {"y": 2})
        self.assertEqual(restored.duration_ms, 1000)


if __name__ == '__main__':
    unittest.main()
