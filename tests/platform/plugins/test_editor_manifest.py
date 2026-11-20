import unittest
from pathlib import Path

import yaml
from pydantic import ValidationError

from src.platform.plugins.manifest import (
    MEDIA_TOOL_SCOPE_VALUES,
    MEDIA_TOOL_SCOPES,
    PluginManifestSchema,
)

MARKETPLACE = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace"

STEP = {"id": "acme.halftone", "editor": "image", "module": "editor/halftone.js", "label": "Halftone"}
TOOL = {"id": "acme.halftone", "editor": "image", "group": "look", "label": "Halftone", "options": "editor/Options.js"}
GROUP = {"id": "acme.stylize", "editor": "image", "label": "Stylize", "order": 450}
OVERLAY = {"id": "acme.magic", "editor": "image", "component": "editor/Magic.js", "produces": "selection"}

ENTRIES = {
    "editor_steps": STEP,
    "editor_tools": TOOL,
    "editor_groups": GROUP,
    "editor_overlays": OVERLAY,
}


def manifest(**overrides):
    data = {
        "id": "acme",
        "name": "Acme",
        "version": "1.0.0",
        "description": "d",
        "author": "a",
        "type": "frontend-only",
    }
    data.update(overrides)
    return data


def validate(**overrides):
    return PluginManifestSchema.model_validate(manifest(**overrides))


class TestEditorSections(unittest.TestCase):
    def test_a_manifest_without_the_keys_has_empty_lists(self):
        schema = validate()
        for key in ENTRIES:
            self.assertEqual(getattr(schema, key), [])

    def test_each_key_accepts_a_valid_entry(self):
        for key, entry in ENTRIES.items():
            schema = validate(**{key: [entry]})
            self.assertEqual(getattr(schema, key)[0].id, entry["id"], key)

    def test_all_three_editor_kinds_are_accepted(self):
        for key, entry in ENTRIES.items():
            for kind in ("image", "video", "audio"):
                validate(**{key: [{**entry, "editor": kind}]})

    def test_unknown_editor_kind_is_refused(self):
        for key, entry in ENTRIES.items():
            with self.assertRaises(ValidationError, msg=key):
                validate(**{key: [{**entry, "editor": "mesh"}]})

    def test_missing_editor_is_refused(self):
        for key, entry in ENTRIES.items():
            bare = {k: v for k, v in entry.items() if k != "editor"}
            with self.assertRaises(ValidationError, msg=key):
                validate(**{key: [bare]})

    def test_missing_component_reference_is_refused(self):
        with self.assertRaises(ValidationError):
            validate(editor_steps=[{k: v for k, v in STEP.items() if k != "module"}])
        with self.assertRaises(ValidationError):
            validate(editor_overlays=[{k: v for k, v in OVERLAY.items() if k != "component"}])

    def test_tool_options_and_bar_are_optional(self):
        schema = validate(editor_tools=[{k: v for k, v in TOOL.items() if k != "options"}])
        self.assertIsNone(schema.editor_tools[0].options)

    def test_tool_ai_flag_is_optional_and_off_by_default(self):
        self.assertFalse(validate(editor_tools=[TOOL]).editor_tools[0].ai)
        self.assertTrue(validate(editor_tools=[{**TOOL, "ai": True}]).editor_tools[0].ai)

    def test_tool_ai_flag_must_be_a_boolean(self):
        with self.assertRaises(ValidationError):
            validate(editor_tools=[{**TOOL, "ai": "sometimes"}])

    def test_unprefixed_or_foreign_prefixed_id_is_refused(self):
        for key, entry in ENTRIES.items():
            for bad in ("halftone", "other.halftone", "acmex.halftone"):
                with self.assertRaises(ValidationError, msg=f"{key} {bad}"):
                    validate(**{key: [{**entry, "id": bad}]})

    def test_duplicate_id_for_the_same_editor_is_refused(self):
        for key, entry in ENTRIES.items():
            with self.assertRaises(ValidationError, msg=key):
                validate(**{key: [entry, dict(entry)]})

    def test_the_same_id_for_different_editors_is_allowed(self):
        for key, entry in ENTRIES.items():
            schema = validate(**{key: [entry, {**entry, "editor": "video"}]})
            self.assertEqual(len(getattr(schema, key)), 2)

    def test_the_same_id_across_different_keys_is_allowed(self):
        schema = validate(editor_steps=[STEP], editor_tools=[TOOL])
        self.assertEqual(schema.editor_steps[0].id, schema.editor_tools[0].id)

    def test_component_paths_cannot_leave_dist(self):
        for bad in ("../x.js", "/abs.js", "a\\b.js", "C:/x.js", "https://evil.test/x.js", "a/../b.js", "frontend/dist/../../x.js", ""):
            with self.assertRaises(ValidationError, msg=bad):
                validate(editor_steps=[{**STEP, "module": bad}])
            with self.assertRaises(ValidationError, msg=bad):
                validate(editor_tools=[{**TOOL, "options": bad}])
            with self.assertRaises(ValidationError, msg=bad):
                validate(editor_overlays=[{**OVERLAY, "component": bad}])
            with self.assertRaises(ValidationError, msg=bad):
                validate(editor_tools=[{**TOOL, "bar": bad}])

    def test_unknown_field_and_bad_values_are_refused(self):
        for key, entry in ENTRIES.items():
            with self.assertRaises(ValidationError, msg=key):
                validate(**{key: [{**entry, "surprise": 1}]})
        with self.assertRaises(ValidationError):
            validate(editor_overlays=[{**OVERLAY, "produces": "mask"}])
        with self.assertRaises(ValidationError):
            validate(editor_overlays=[{**OVERLAY, "cursor": "pointer"}])

    def test_defaults(self):
        self.assertEqual(validate(editor_steps=[STEP]).editor_steps[0].icon, "extension")
        self.assertEqual(validate(editor_groups=[{k: v for k, v in GROUP.items() if k != "order"}]).editor_groups[0].order, 500)

    def test_task_may_name_its_rail_tool(self):
        task = {"id": "acme.relight", "label": "Relight", "tool": "acme.relight"}
        self.assertEqual(validate(editor_tasks=[task]).editor_tasks[0].tool, "acme.relight")
        self.assertEqual(validate(editor_tasks=[{**task, "step_kind": "generate"}]).editor_tasks[0].step_kind, "generate")
        self.assertIsNone(validate(editor_tasks=[{k: v for k, v in task.items() if k != "tool"}]).editor_tasks[0].tool)

    def test_every_shipped_marketplace_manifest_still_validates(self):
        manifests = sorted(MARKETPLACE.glob("*/manifest.yml"))
        self.assertTrue(manifests)
        for path in manifests:
            PluginManifestSchema.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class TestMediaToolScopes(unittest.TestCase):
    def _tool(self, **overrides):
        entry = {"id": "acme.loop", "label": "Loop", "accepts": ["image"], "component": "Loop.js"}
        entry.update(overrides)
        return validate(media_tools=[entry]).media_tools[0]

    def test_the_default_scopes_are_exactly_these(self):
        self.assertEqual(MEDIA_TOOL_SCOPES, ("library", "history", "field"))
        self.assertEqual(self._tool().scopes, ["library", "history", "field"])

    def test_editor_is_allowed_but_not_in_the_default(self):
        self.assertEqual(MEDIA_TOOL_SCOPE_VALUES, ("library", "history", "field", "editor"))
        self.assertNotIn("editor", self._tool().scopes)
        self.assertEqual(self._tool(scopes=["editor"]).scopes, ["editor"])
        self.assertEqual(self._tool(scopes=["library", "editor"]).scopes, ["library", "editor"])

    def test_an_unknown_or_empty_scope_is_refused(self):
        for scopes in (["sidebar"], [], ["editor", "rail"]):
            with self.assertRaises(ValidationError, msg=str(scopes)):
                self._tool(scopes=scopes)
