from types import SimpleNamespace

from src.platform.plugins.manifest import PluginManifestSchema
from src.platform.plugins.registry import PluginRegistry
from tests.features.media_tools.support import MediaToolsTestBase


class TestPluginEditorScopeInCatalog(MediaToolsTestBase):
    def setUp(self):
        super().setUp()
        schema = PluginManifestSchema.model_validate({
            "id": "acme",
            "name": "Acme",
            "version": "1.0.0",
            "description": "d",
            "author": "a",
            "type": "frontend-only",
            "media_tools": [
                {"id": "acme.rail", "label": "Rail", "accepts": ["image"], "component": "Rail.js", "scopes": ["editor"]},
                {"id": "acme.plain", "label": "Plain", "accepts": ["image"], "component": "Plain.js"},
            ],
        })
        manifest = SimpleNamespace(id="acme", media_tools=[tool.model_dump() for tool in schema.media_tools])
        registrar = PluginRegistry.__new__(PluginRegistry)
        registrar.media_tool_registry = self.registry
        self.assertIsNone(registrar._register_plugin_media_tools(manifest))

    def catalog(self, **query):
        response = self.client.get("/api/media-tools", params=query)
        self.assertEqual(response.status_code, 200, response.text)
        return {tool["id"]: tool for tool in response.json()["data"]["tools"]}

    def test_an_editor_scoped_plugin_tool_reaches_the_catalog_with_its_scope(self):
        tools = self.catalog(scope="editor")
        self.assertEqual(tools["acme.rail"]["scopes"], ["editor"])
        self.assertNotIn("acme.plain", tools)

    def test_a_tool_without_scopes_never_joins_the_editor_rail(self):
        every = self.catalog()
        self.assertEqual(every["acme.plain"]["scopes"], ["library", "history", "field"])
        self.assertIn("acme.rail", every)
        self.assertNotIn("acme.plain", self.catalog(scope="editor"))
        self.assertIn("acme.plain", self.catalog(scope="library"))
        self.assertNotIn("acme.rail", self.catalog(scope="library"))
