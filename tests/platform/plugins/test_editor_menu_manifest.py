import unittest

from pydantic import ValidationError

from src.platform.plugins.manifest import PluginManifestSchema

ITEM = {
    "id": "acme.note",
    "editor": "image",
    "menu": "tools",
    "label": "Note",
    "module": "editor/note.js",
}


def validate(**overrides):
    data = {
        "id": "acme",
        "name": "Acme",
        "version": "1.0.0",
        "description": "d",
        "author": "a",
        "type": "frontend-only",
    }
    data.update(overrides)
    return PluginManifestSchema.model_validate(data)


class TestEditorMenuSection(unittest.TestCase):
    def test_a_manifest_without_the_key_has_an_empty_list(self):
        self.assertEqual(validate().editor_menu, [])

    def test_an_entry_gets_its_defaults(self):
        entry = validate(editor_menu=[ITEM]).editor_menu[0]
        self.assertEqual(entry.group, "plugins")
        self.assertEqual(entry.icon, "extension")
        self.assertIsNone(entry.order)
        self.assertIsNone(entry.keys)

    def test_all_three_editor_kinds_are_accepted(self):
        for kind in ("image", "video", "audio"):
            validate(editor_menu=[{**ITEM, "editor": kind}])

    def test_a_plugin_menu_id_is_accepted(self):
        entry = validate(editor_menu=[{**ITEM, "menu": "acme.shelf", "menu_label": "Shelf"}]).editor_menu[0]
        self.assertEqual(entry.menu_label, "Shelf")

    def test_the_id_must_belong_to_the_plugin(self):
        with self.assertRaises(ValidationError):
            validate(editor_menu=[{**ITEM, "id": "other.note"}])

    def test_an_id_declared_twice_for_one_editor_is_refused(self):
        with self.assertRaises(ValidationError):
            validate(editor_menu=[ITEM, ITEM])

    def test_the_module_must_stay_inside_dist(self):
        for bad in ("../x.js", "/abs.js", "a\\b.js", "c:x.js"):
            with self.assertRaises(ValidationError, msg=bad):
                validate(editor_menu=[{**ITEM, "module": bad}])

    def test_the_module_is_required(self):
        item = {key: value for key, value in ITEM.items() if key != "module"}
        with self.assertRaises(ValidationError):
            validate(editor_menu=[item])

    def test_unknown_keys_are_refused(self):
        with self.assertRaises(ValidationError):
            validate(editor_menu=[{**ITEM, "run": "x"}])

    def test_a_menu_id_with_spaces_is_refused(self):
        with self.assertRaises(ValidationError):
            validate(editor_menu=[{**ITEM, "menu": "My Menu"}])


if __name__ == "__main__":
    unittest.main()
