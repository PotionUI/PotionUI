import shutil
import tempfile
import unittest
from pathlib import Path

import yaml
from pydantic import ValidationError

from src.features.models.type_repository import verdict_is_current
from src.platform.plugins.manifest import PluginManifestSchema
from src.platform.plugins.registry import PluginRegistry, PluginState
from src.platform.runtime.model_headers import classify_header, model_classifier_registry
from tests.fixtures.model_header_fixtures import make_view

PLUGIN_CODE = '''
from src.plugin_api.models import FamilyMatch


def classify_hidream(view):
    if "hidream.marker" in view.denoiser_keys:
        return FamilyMatch("hidream")
    return None


def classify_greedy(view):
    if "hidream.marker" in view.denoiser_keys:
        return FamilyMatch("greedy")
    return None


def classify_broken(view):
    raise RuntimeError("boom")
'''

MARKER = {"model.diffusion_model.hidream.marker": (4,), "model.diffusion_model.other": (4,)}


def minimal(**overrides):
    data = {
        'id': 'test-plugin',
        'name': 'Test Plugin',
        'version': '1.0.0',
        'description': 'A test plugin',
        'author': 'Test Author',
        'type': 'backend-only',
    }
    data.update(overrides)
    return data


def entry(**overrides):
    data = {
        'key': 'hidream',
        'handler': 'classifiers.classify_hidream',
        'label': 'HiDream',
        'version': 1,
        'formats': ['safetensors'],
    }
    data.update(overrides)
    return data


class TestModelClassifierManifest(unittest.TestCase):
    def test_a_full_entry_parses(self):
        schema = PluginManifestSchema.model_validate(
            minimal(model_classifiers=[entry(formats=['safetensors', 'gguf'], priority=5)])
        )
        parsed = schema.model_classifiers[0]
        self.assertEqual((parsed.key, parsed.version, parsed.priority), ('hidream', 1, 5))
        self.assertEqual(parsed.formats, ['safetensors', 'gguf'])

    def test_priority_defaults_to_zero(self):
        schema = PluginManifestSchema.model_validate(minimal(model_classifiers=[entry()]))
        self.assertEqual(schema.model_classifiers[0].priority, 0)

    def test_the_section_defaults_to_empty(self):
        self.assertEqual(PluginManifestSchema.model_validate(minimal()).model_classifiers, [])

    def test_unknown_formats_are_rejected(self):
        with self.assertRaises(ValidationError):
            PluginManifestSchema.model_validate(minimal(model_classifiers=[entry(formats=['pickle'])]))

    def test_empty_formats_are_rejected(self):
        with self.assertRaises(ValidationError):
            PluginManifestSchema.model_validate(minimal(model_classifiers=[entry(formats=[])]))

    def test_a_version_below_one_is_rejected(self):
        with self.assertRaises(ValidationError):
            PluginManifestSchema.model_validate(minimal(model_classifiers=[entry(version=0)]))

    def test_missing_and_empty_required_fields_are_rejected(self):
        for field in ('key', 'handler', 'label', 'version', 'formats'):
            data = entry()
            del data[field]
            with self.assertRaises(ValidationError, msg=field):
                PluginManifestSchema.model_validate(minimal(model_classifiers=[data]))
        with self.assertRaises(ValidationError):
            PluginManifestSchema.model_validate(minimal(model_classifiers=[entry(handler='')]))

    def test_a_handler_without_a_module_is_rejected(self):
        for handler in ('classify', 'classifiers:classify', '.classify', 'classifiers.'):
            with self.assertRaises(ValidationError, msg=handler):
                PluginManifestSchema.model_validate(minimal(model_classifiers=[entry(handler=handler)]))

    def test_unknown_entry_keys_are_rejected(self):
        with self.assertRaises(ValidationError):
            PluginManifestSchema.model_validate(minimal(model_classifiers=[entry(extra='x')]))


class TestModelClassifierRegistration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()
        self.registry = PluginRegistry(str(self.marketplace_dir), str(self.local_dir))
        self.enabled = []

    def tearDown(self):
        for plugin_id in self.enabled:
            model_classifier_registry.unregister_source(plugin_id)
        shutil.rmtree(self.temp_dir)

    def _create_plugin(self, plugin_id, classifiers):
        self.enabled.append(plugin_id)
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()
        data = minimal(id=plugin_id, name=plugin_id, model_classifiers=classifiers)
        with open(plugin_dir / "manifest.yml", 'w', encoding="utf-8") as f:
            yaml.dump(data, f)
        (plugin_dir / "classifiers.py").write_text(PLUGIN_CODE, encoding="utf-8")

    def test_enable_registers_with_the_plugin_as_source(self):
        self._create_plugin('cls-plugin', [entry(priority=7)])

        self.assertTrue(self.registry.enable_plugin('cls-plugin'))

        definition = model_classifier_registry.get('hidream')
        self.assertEqual(definition.source, 'cls-plugin')
        self.assertEqual((definition.label, definition.version, definition.priority), ('HiDream', 1, 7))
        self.assertEqual(definition.formats, ('safetensors',))

    def test_disable_removes_the_classifier_and_restores_the_fingerprint(self):
        before = model_classifier_registry.fingerprint()
        self._create_plugin('cls-plugin-off', [entry()])
        self.assertTrue(self.registry.enable_plugin('cls-plugin-off'))
        self.assertNotEqual(model_classifier_registry.fingerprint(), before)

        self.assertTrue(self.registry.disable_plugin('cls-plugin-off'))

        self.assertIsNone(model_classifier_registry.get('hidream'))
        self.assertEqual(model_classifier_registry.fingerprint(), before)

    def test_enable_fails_when_a_key_collides_with_core(self):
        self._create_plugin('cls-collide', [entry(key='core.native_dit')])

        self.assertFalse(self.registry.enable_plugin('cls-collide'))
        self.assertEqual(self.registry.get_plugin_state('cls-collide'), PluginState.ERROR)
        self.assertIn('core.native_dit', self.registry.get_plugin_error('cls-collide'))
        self.assertEqual(model_classifier_registry.get('core.native_dit').source, 'core')

    def test_enable_fails_when_the_handler_cannot_be_loaded(self):
        self._create_plugin('cls-ghost', [entry(handler='classifiers.no_such_function')])

        self.assertFalse(self.registry.enable_plugin('cls-ghost'))
        self.assertIn('classifiers.no_such_function', self.registry.get_plugin_error('cls-ghost'))
        self.assertIsNone(model_classifier_registry.get('hidream'))

    def test_a_failing_second_entry_rolls_back_the_first(self):
        self._create_plugin(
            'cls-half',
            [entry(), entry(key='second', handler='classifiers.no_such_function')],
        )

        self.assertFalse(self.registry.enable_plugin('cls-half'))
        self.assertIsNone(model_classifier_registry.get('hidream'))

    def test_a_plugin_decides_a_file_that_was_undecided_before(self):
        view = make_view(MARKER)
        before = model_classifier_registry.fingerprint()
        undecided = classify_header(view)
        self.assertFalse(undecided.decided)
        stored = {
            'status': 'undecided', 'transformer_extractable': 0, 'registry_fingerprint': before,
        }
        self.assertTrue(verdict_is_current(stored, before))

        self._create_plugin('cls-decide', [entry()])
        self.assertTrue(self.registry.enable_plugin('cls-decide'))

        self.assertFalse(verdict_is_current(stored, model_classifier_registry.fingerprint()))
        decided = classify_header(view)
        self.assertEqual((decided.decided, decided.family, decided.classifier), (True, 'hidream', 'hidream'))
        self.assertEqual(decided.model_type, 'diffusion_model')

    def test_priority_orders_plugin_classifiers(self):
        self._create_plugin(
            'cls-order',
            [
                entry(key='low', handler='classifiers.classify_hidream', priority=1),
                entry(key='high', handler='classifiers.classify_greedy', priority=2),
            ],
        )
        self.assertTrue(self.registry.enable_plugin('cls-order'))

        self.assertEqual(classify_header(make_view(MARKER)).family, 'greedy')

    def test_a_plugin_can_outrank_core_and_a_lower_one_cannot(self):
        flux_and_marker = {
            "double_blocks.0.img_attn.norm.key_norm.scale": (4,),
            "img_in.weight": (4, 4),
            "hidream.marker": (4,),
        }
        self._create_plugin('cls-low', [entry(priority=1)])
        self._create_plugin('cls-high', [entry(key='greedy', handler='classifiers.classify_greedy', priority=500)])
        self.assertTrue(self.registry.enable_plugin('cls-low'))
        self.assertEqual(classify_header(make_view(flux_and_marker)).family, 'flux')

        self.assertTrue(self.registry.enable_plugin('cls-high'))
        self.assertEqual(classify_header(make_view(flux_and_marker)).family, 'greedy')

    def test_a_classifier_is_only_offered_its_formats(self):
        self._create_plugin('cls-gguf', [entry(formats=['gguf'])])
        self.assertTrue(self.registry.enable_plugin('cls-gguf'))

        self.assertFalse(classify_header(make_view(MARKER)).decided)
        self.assertTrue(classify_header(make_view(MARKER, fmt='gguf')).decided)

    def test_a_raising_plugin_classifier_is_skipped(self):
        self._create_plugin(
            'cls-raise',
            [
                entry(key='broken', handler='classifiers.classify_broken', priority=9),
                entry(),
            ],
        )
        self.assertTrue(self.registry.enable_plugin('cls-raise'))

        self.assertEqual(classify_header(make_view(MARKER)).family, 'hidream')
