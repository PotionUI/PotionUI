from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
import json


@dataclass
class Automation:
    """A user-authored automation: a node graph of triggers, conditions, and actions."""
    id: str
    name: str
    graph: Dict[str, Any]
    description: Optional[str] = None
    enabled: bool = False
    version: int = 1
    user_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_run_status: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> 'Automation':
        return cls(
            id=row['id'],
            name=row['name'],
            description=row['description'],
            enabled=bool(row['enabled']),
            graph=json.loads(row['graph']),
            version=row['version'],
            user_id=row['user_id'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None,
            last_run_at=datetime.fromisoformat(row['last_run_at']) if row['last_run_at'] else None,
            last_run_status=row['last_run_status'],
        )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'enabled': self.enabled,
            'graph': self.graph,
            'version': self.version,
            'user_id': self.user_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'last_run_at': self.last_run_at.isoformat() if self.last_run_at else None,
            'last_run_status': self.last_run_status,
        }

    def serialize_graph(self) -> str:
        return json.dumps(self.graph)


@dataclass
class AutomationRun:
    """A single execution of an automation, starting from one trigger node."""
    id: str
    automation_id: str
    trigger_node_id: Optional[str] = None
    trigger_type: Optional[str] = None
    status: str = 'running'
    event_payload: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None

    @classmethod
    def from_row(cls, row) -> 'AutomationRun':
        return cls(
            id=row['id'],
            automation_id=row['automation_id'],
            trigger_node_id=row['trigger_node_id'],
            trigger_type=row['trigger_type'],
            status=row['status'],
            event_payload=json.loads(row['event_payload']) if row['event_payload'] else None,
            error=row['error'],
            started_at=datetime.fromisoformat(row['started_at']) if row['started_at'] else None,
            finished_at=datetime.fromisoformat(row['finished_at']) if row['finished_at'] else None,
            duration_ms=row['duration_ms'],
        )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'automation_id': self.automation_id,
            'trigger_node_id': self.trigger_node_id,
            'trigger_type': self.trigger_type,
            'status': self.status,
            'event_payload': self.event_payload,
            'error': self.error,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
            'duration_ms': self.duration_ms,
        }

    def serialize_event_payload(self) -> Optional[str]:
        return json.dumps(self.event_payload) if self.event_payload is not None else None


@dataclass
class AutomationRunNode:
    """Per-node execution status/result for a single AutomationRun."""
    id: str
    run_id: str
    node_id: str
    node_type: str
    status: str = 'running'
    input: Optional[Any] = None
    output: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None

    @classmethod
    def from_row(cls, row) -> 'AutomationRunNode':
        return cls(
            id=row['id'],
            run_id=row['run_id'],
            node_id=row['node_id'],
            node_type=row['node_type'],
            status=row['status'],
            input=json.loads(row['input']) if row['input'] else None,
            output=json.loads(row['output']) if row['output'] else None,
            error=row['error'],
            started_at=datetime.fromisoformat(row['started_at']) if row['started_at'] else None,
            finished_at=datetime.fromisoformat(row['finished_at']) if row['finished_at'] else None,
            duration_ms=row['duration_ms'],
        )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'run_id': self.run_id,
            'node_id': self.node_id,
            'node_type': self.node_type,
            'status': self.status,
            'input': self.input,
            'output': self.output,
            'error': self.error,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
            'duration_ms': self.duration_ms,
        }

    def serialize_input(self) -> Optional[str]:
        return json.dumps(self.input) if self.input is not None else None

    def serialize_output(self) -> Optional[str]:
        return json.dumps(self.output) if self.output is not None else None
