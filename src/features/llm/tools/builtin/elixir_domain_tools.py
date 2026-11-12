import asyncio
import uuid
from typing import Any, Dict

from src.features.elixirs.listing import summaries
from src.features.elixirs.brewing import begin_brew, run_detail
from src.features.llm.tools.base import ToolContext, ToolResult
from src.features.llm.tools.builtin.domain_support import (
    DomainTool,
    acting_user,
    failure,
    payload,
    required_text,
)

NODE_KEYS = ("id", "kind", "label", "name", "preset_id", "mode", "type")
RUN_MODES = ("review", "automatic")


class ElixirTool(DomainTool):
    domain = "elixirs"
    group = "Elixirs"
    context_group = "library"

    def _elixir_id_property(self) -> Dict[str, Any]:
        return {"type": "string", "description": "The elixir id from list_elixirs."}


class ListElixirsTool(ElixirTool):
    icon = "flask-conical"
    name = "list_elixirs"
    read_only = True
    user_description = "Lists your elixirs (saved multi-step generation recipes)."

    @property
    def description(self) -> str:
        return (
            "List the user's elixirs: saved graphs of generation steps that run as one brew. Each entry has "
            "id, name, run_mode (review or automatic), revision and a short summary. Use describe_elixir for "
            "one elixir's nodes, then run_elixir to brew it."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        repository = self.service(context).repository

        def load():
            records = repository.list(context.user_id)
            extras = summaries(repository, records, context.user_id)
            return [{**record.to_summary(), **extras[record.id]} for record in records]

        return payload({"elixirs": await asyncio.to_thread(load)})


class DescribeElixirTool(ElixirTool):
    icon = "flask-conical"
    name = "describe_elixir"
    read_only = True
    user_description = "Shows one elixir: its steps, connections, run mode and revision."

    @property
    def description(self) -> str:
        return (
            "Describe one of the user's elixirs: name, run_mode, revision, its nodes (id, kind, preset) and "
            "the connections between them. The revision is what run_elixir brews against."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"elixir_id": self._elixir_id_property()}, "required": ["elixir_id"]}

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        elixir_id = required_text(kwargs, "elixir_id")
        if not elixir_id:
            return failure("'elixir_id' is required")
        record = await asyncio.to_thread(self.service(context).repository.get, elixir_id, context.user_id)
        if record is None:
            return failure("That elixir does not exist.")
        full = record.to_full()
        definition = full.get("definition") or {}
        return payload({
            "id": full.get("id"),
            "name": full.get("name"),
            "run_mode": full.get("run_mode"),
            "revision": full.get("revision"),
            "nodes": [
                {key: node[key] for key in NODE_KEYS if key in node} for node in definition.get("nodes") or []
            ],
            "edges": [
                {key: edge.get(key) for key in ("id", "source", "source_handle", "target", "target_handle")}
                for edge in definition.get("edges") or []
            ],
        })


class RunElixirTool(ElixirTool):
    icon = "play"
    name = "run_elixir"
    spends = True
    user_description = "Brews one of your elixirs."

    @property
    def description(self) -> str:
        return (
            "Brew one of the user's elixirs. Runs the whole graph in its run mode (review pauses for the user "
            "to choose outputs; automatic goes through). 'expected_revision' (from describe_elixir) guards "
            "against brewing a graph that changed meanwhile; leave it out to brew the current one. Returns the "
            "brew with its run id: follow it with get_elixir_run. Repeating the same 'idempotency_key' never "
            "starts a second brew."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "elixir_id": self._elixir_id_property(),
                "expected_revision": {"type": "integer", "minimum": 1},
                "run_mode": {"type": "string", "enum": list(RUN_MODES)},
                "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 120},
            },
            "required": ["elixir_id"],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        elixir_id = required_text(kwargs, "elixir_id")
        if not elixir_id:
            return failure("'elixir_id' is required")
        run_mode = kwargs.get("run_mode")
        if run_mode is not None and run_mode not in RUN_MODES:
            return failure(f"'run_mode' must be one of {', '.join(RUN_MODES)}.")
        elixirs = self.service(context)
        revision = kwargs.get("expected_revision")
        if revision is None:
            record = await asyncio.to_thread(elixirs.repository.get, elixir_id, context.user_id)
            if record is None:
                return failure("That elixir does not exist.")
            revision = record.revision
        key = required_text(kwargs, "idempotency_key") or uuid.uuid4().hex
        brew = await asyncio.to_thread(
            begin_brew, elixirs, acting_user(context), elixir_id, int(revision), run_mode, {}, None, key,
        )
        return payload(brew)


class GetElixirRunTool(ElixirTool):
    icon = "activity"
    name = "get_elixir_run"
    read_only = True
    user_description = "Shows how one of your elixir brews is going."

    @property
    def description(self) -> str:
        return (
            "Check one of the user's elixir brews by run id (from run_elixir): its status, each step's state "
            "and progress, and the generation ids behind them (read those with get_generation_results)."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"run_id": {"type": "string", "description": "The run id run_elixir returned."}},
            "required": ["run_id"],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        run_id = required_text(kwargs, "run_id")
        if not run_id:
            return failure("'run_id' is required")
        coordinator = self.service(context).coordinator

        def load():
            run = coordinator.store.get_run(run_id, context.user_id)
            return None if run is None else run_detail(coordinator, acting_user(context), run)

        detail = await asyncio.to_thread(load)
        if detail is None:
            return failure("That brew does not exist.")
        return payload(detail)


ELIXIR_DOMAIN_TOOLS = (ListElixirsTool, DescribeElixirTool, RunElixirTool, GetElixirRunTool)
