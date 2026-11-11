import asyncio
import json

from src.features.llm.tools.base import BaseTool, ToolResult, ToolServices
from src.features.llm.tools.builtin import register_builtin_tools
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp.tool_policy import POLICY_ALL_ON
from src.features.mcp.tool_repository import McpToolRepository
from tests.features.mcp.support import build_collaborators, handle_method
from tests.features.media_tools.support import MediaToolsTestBase


class McpMediaToolCase(MediaToolsTestBase):

    def setUp(self):
        super().setUp()
        self.tools = ToolRegistry()
        register_builtin_tools(self.tools, self.registry)
        self.collaborators = build_collaborators(self.tools, ToolServices(media_tool_service=self.service))
        McpToolRepository().apply_default(self.user_id, POLICY_ALL_ON, {})

    def rpc(self, method, params=None, user_id=None):
        return asyncio.run(handle_method(self.collaborators, method, params or {}, user_id or self.user_id))

    def call(self, arguments, user_id=None):
        return self.rpc("tools/call", {"name": "run_media_tool", "arguments": arguments}, user_id)

    def listed(self):
        return {tool["name"]: tool for tool in self.rpc("tools/list")["tools"]}

    def text(self, result):
        return result["content"][0]["text"]


class TestMcpListsRunMediaTool(McpMediaToolCase):

    def test_run_media_tool_is_listed_with_a_schema_built_from_the_catalog(self):
        tool = self.listed()["run_media_tool"]

        enum = set(tool["inputSchema"]["properties"]["steps"]["items"]["properties"]["tool"]["enum"])
        self.assertIn("shrink", enum)
        self.assertIn("acme.mirror", enum)
        self.assertNotIn("acme.panel", enum)
        self.assertEqual(tool["inputSchema"]["required"], ["steps", "items"])
        self.assertIn("- acme.mirror (image): size int[1..64] =8", tool["description"])
        self.assertIn("list_library_items", tool["description"])

    def test_a_disabled_plugin_tool_disappears_from_the_listing(self):
        self.assertIn("acme.mirror", self.listed()["run_media_tool"]["description"])

        self.registry.unregister_source("acme")

        tool = self.listed()["run_media_tool"]
        self.assertNotIn("acme.mirror", tool["description"])
        self.assertNotIn("acme.mirror", tool["inputSchema"]["properties"]["steps"]["items"]["properties"]["tool"]["enum"])


class TestMcpCallsRunMediaTool(McpMediaToolCase):

    def test_a_new_mode_call_runs_and_returns_the_items(self):
        item = self.image_item()

        result = self.call({"steps": [{"tool": "shrink", "params": {"size": 11}}], "items": [{"item_id": item.id}]})

        self.assertFalse(result["isError"])
        data = json.loads(self.text(result))
        self.assertEqual(data["status"], "completed")
        self.assertEqual((data["items"][0]["width"], data["items"][0]["height"]), (11, 11))
        self.assertEqual(len(self.upload_rows()), 2)

    def test_a_replace_call_runs_through_the_confirmed_path_with_no_second_approval(self):
        item = self.image_item()

        result = self.call({
            "steps": [{"tool": "shrink", "params": {"size": 10}}],
            "items": [{"item_id": item.id}],
            "mode": "replace",
        })

        self.assertFalse(result["isError"], self.text(result))
        data = json.loads(self.text(result))
        self.assertTrue(data["replaced"])
        self.assertEqual(data["items"][0]["id"], item.id)
        self.assertNotEqual(self.upload_repo.get_by_id(item.id, self.user_id).filename, item.filename)
        self.assertEqual(len(self.upload_rows()), 1)

    def test_a_refused_replace_is_an_error_not_a_silent_new_item(self):
        record, _path = self.generation_file()

        result = self.call({
            "steps": [{"tool": "shrink"}],
            "items": [{"generation_file_id": record.id}],
            "mode": "replace",
        })

        self.assertTrue(result["isError"])
        self.assertIn("replace_not_allowed", self.text(result))
        self.assertEqual(self.upload_rows(), [])

    def test_errors_come_back_with_the_api_code_and_message(self):
        item = self.image_item()

        unknown = self.call({"steps": [{"tool": "nope"}], "items": [{"item_id": item.id}]})
        invalid = self.call({"steps": [{"tool": "shrink", "params": {"size": 0}}], "items": [{"item_id": item.id}]})

        self.assertTrue(unknown["isError"])
        self.assertTrue(self.text(unknown).startswith("Error: unknown_tool (step 1): Unknown tool: nope."))
        self.assertTrue(invalid["isError"])
        self.assertIn("invalid_params (step 1, 'size')", self.text(invalid))

    def test_another_users_item_cannot_be_edited_over_mcp(self):
        foreign = self.image_item(user_id=self.other_user_id)

        result = self.call({"steps": [{"tool": "shrink"}], "items": [{"item_id": foreign.id}]})

        self.assertTrue(result["isError"])
        self.assertIn("not_found", self.text(result))
        self.assertEqual(self.upload_rows(), [])
        self.assertEqual(len(self.upload_rows(self.other_user_id)), 1)

    def test_the_caller_edits_their_own_item_while_the_other_user_cannot_see_the_result(self):
        mine = self.image_item()

        result = self.call({"steps": [{"tool": "shrink"}], "items": [{"item_id": mine.id}]})

        created = json.loads(self.text(result))["items"][0]["id"]
        self.assertIsNotNone(self.upload_repo.get_by_id(created, self.user_id))
        self.assertIsNone(self.upload_repo.get_by_id(created, self.other_user_id))


class _Switch(BaseTool):
    name = "switch"
    modes = ["generation"]
    mcp = True
    requires_approval = True

    def __init__(self):
        self.ran = []

    @property
    def description(self):
        return "switch"

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "required": []}

    def approval_required(self, arguments):
        return bool(arguments.get("risky"))

    async def execute(self, context, **kwargs):
        self.ran.append(("execute", kwargs))
        return ToolResult(success=True, data="ran")

    async def execute_confirmed(self, context, **kwargs):
        self.ran.append(("execute_confirmed", kwargs))
        return ToolResult(success=True, data="confirmed")


class TestPerCallApprovalOverMcp(McpMediaToolCase):

    def test_mcp_confirms_only_the_calls_the_tool_says_need_approval(self):
        switch = _Switch()
        self.tools.register(switch)

        safe = self.rpc("tools/call", {"name": "switch", "arguments": {}})
        risky = self.rpc("tools/call", {"name": "switch", "arguments": {"risky": True}})

        self.assertEqual(self.text(safe), "ran")
        self.assertEqual(self.text(risky), "confirmed")
        self.assertEqual([step for step, _ in switch.ran], ["execute", "execute_confirmed"])
