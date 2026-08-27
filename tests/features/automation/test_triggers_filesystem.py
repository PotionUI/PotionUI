"""Tests for the filesystem trigger's directory picker and effective-directory resolution."""

import os
import tempfile
import unittest
from pathlib import Path

from src.features.automation.triggers.filesystem import (
    CUSTOM_PATH_VALUE,
    FilesystemTrigger,
    build_event_payload,
    list_app_directories,
    resolve_effective_directory,
)


class FakeSettings:
    def __init__(self, models_dir, storage_dir, generations_dir):
        self._models_dir = models_dir
        self._storage_dir = storage_dir
        self._generations_dir = generations_dir

    def get_models_dir(self, user_id=None):
        return self._models_dir

    def get_file_storage_directory(self, user_id=None):
        return self._storage_dir

    def get_generations_directory(self, user_id=None):
        return self._generations_dir


class TestListAppDirectories(unittest.TestCase):

    def test_enumerates_models_root_subdirs_storage_outputs_and_custom(self):
        with tempfile.TemporaryDirectory() as tmp:
            models_dir = os.path.join(tmp, "models")
            os.makedirs(os.path.join(models_dir, "loras"))
            os.makedirs(os.path.join(models_dir, "checkpoints"))
            # A file directly under models/ should not show up as a subdirectory option.
            with open(os.path.join(models_dir, "readme.txt"), "w") as f:
                f.write("x")

            settings = FakeSettings(
                models_dir=models_dir,
                storage_dir=os.path.join(tmp, "storage"),
                generations_dir=os.path.join(tmp, "storage", "generations"),
            )

            options = list_app_directories(settings)
            values = [o["value"] for o in options]

            self.assertIn(models_dir, values)
            self.assertIn(os.path.join(models_dir, "loras"), values)
            self.assertIn(os.path.join(models_dir, "checkpoints"), values)
            self.assertIn(os.path.join(tmp, "storage"), values)
            self.assertIn(os.path.join(tmp, "storage", "generations"), values)
            self.assertEqual(values[-1], CUSTOM_PATH_VALUE)
            self.assertNotIn(os.path.join(models_dir, "readme.txt"), values)

    def test_missing_models_dir_does_not_raise(self):
        settings = FakeSettings(
            models_dir="/does/not/exist/anywhere",
            storage_dir="storage",
            generations_dir="storage/generations",
        )

        options = list_app_directories(settings)

        # Still returns the base options even though subdirectory enumeration failed.
        values = [o["value"] for o in options]
        self.assertIn("/does/not/exist/anywhere", values)
        self.assertEqual(values[-1], CUSTOM_PATH_VALUE)


class TestResolveEffectiveDirectory(unittest.TestCase):

    def test_app_directory_choice_resolves_to_its_value(self):
        config = {"directory": "models/loras"}
        self.assertEqual(resolve_effective_directory(config), "models/loras")

    def test_custom_choice_resolves_to_custom_path(self):
        config = {"directory": CUSTOM_PATH_VALUE, "custom_path": "/srv/incoming/loras"}
        self.assertEqual(resolve_effective_directory(config), "/srv/incoming/loras")

    def test_custom_choice_without_custom_path_resolves_empty(self):
        config = {"directory": CUSTOM_PATH_VALUE}
        self.assertEqual(resolve_effective_directory(config), "")

    def test_custom_path_is_stripped(self):
        config = {"directory": CUSTOM_PATH_VALUE, "custom_path": "  /srv/incoming/loras  "}
        self.assertEqual(resolve_effective_directory(config), "/srv/incoming/loras")

    def test_missing_directory_resolves_empty(self):
        self.assertEqual(resolve_effective_directory({}), "")


class TestBuildEventPayload(unittest.TestCase):

    def test_rel_parts_and_rel_path_for_a_nested_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            watch_dir = os.path.join(tmp, "loras")
            os.makedirs(os.path.join(watch_dir, "krea2"))
            file_path = os.path.join(watch_dir, "krea2", "x.safetensors")
            Path(file_path).touch()

            payload = build_event_payload(watch_dir, file_path, "created")

            self.assertEqual(payload["rel_parts"], ["krea2", "x.safetensors"])
            # "parts" kept for back-compat, identical to rel_parts.
            self.assertEqual(payload["parts"], payload["rel_parts"])
            self.assertEqual(payload["rel_path"], "krea2/x.safetensors")
            self.assertEqual(payload["path"], file_path)
            self.assertEqual(payload["dir"], watch_dir)
            self.assertEqual(payload["event"], "created")
            self.assertEqual(payload["ext"], ".safetensors")

    def test_rel_parts_for_a_file_directly_in_the_watched_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = os.path.join(tmp, "x.safetensors")
            Path(file_path).touch()

            payload = build_event_payload(tmp, file_path, "created")

            self.assertEqual(payload["rel_parts"], ["x.safetensors"])
            self.assertEqual(payload["rel_path"], "x.safetensors")


class TestFilesystemTriggerRecursiveFiltering(unittest.TestCase):
    """`recursive` defaults true (subdirectory events fire); false restricts to direct children."""

    def _make_trigger(self, watch_dir: str, recursive: bool) -> FilesystemTrigger:
        return FilesystemTrigger(
            automation_id="auto1", node_id="node1",
            config={"directory": watch_dir, "recursive": recursive},
            enqueue=lambda *a: None, watch_registry=None,
        )

    def test_recursive_true_fires_for_subdirectory_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "krea2"))
            trigger = self._make_trigger(tmp, recursive=True)
            fired = []
            trigger.fire = lambda payload: fired.append(payload)

            trigger._on_event("created", os.path.join(tmp, "krea2", "x.safetensors"))

            self.assertEqual(len(fired), 1)

    def test_recursive_false_ignores_subdirectory_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "krea2"))
            trigger = self._make_trigger(tmp, recursive=False)
            fired = []
            trigger.fire = lambda payload: fired.append(payload)

            trigger._on_event("created", os.path.join(tmp, "krea2", "x.safetensors"))

            self.assertEqual(fired, [])

    def test_recursive_false_still_fires_for_direct_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            trigger = self._make_trigger(tmp, recursive=False)
            fired = []
            trigger.fire = lambda payload: fired.append(payload)

            trigger._on_event("created", os.path.join(tmp, "x.safetensors"))

            self.assertEqual(len(fired), 1)

    def test_recursive_defaults_true_when_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "krea2"))
            trigger = FilesystemTrigger(
                automation_id="auto1", node_id="node1",
                config={"directory": tmp}, enqueue=lambda *a: None, watch_registry=None,
            )
            fired = []
            trigger.fire = lambda payload: fired.append(payload)

            trigger._on_event("created", os.path.join(tmp, "krea2", "x.safetensors"))

            self.assertEqual(len(fired), 1)


if __name__ == '__main__':
    unittest.main()
