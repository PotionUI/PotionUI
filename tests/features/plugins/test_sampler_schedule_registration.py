"""A plugin's `samplers:` / `schedules:` manifest roots reach the native
sampling core: registered on enable, actually dispatched to by `denoise()` and
`build_sigmas()`, and gone again on disable."""

import shutil
import tempfile
import unittest
from pathlib import Path

import torch
import yaml

from src.platform.plugins.registry import PluginRegistry, PluginState
from src.platform.runtime.native.sampling.denoise_loop import denoise
from src.platform.runtime.native.sampling.flow_schedule import build_sigmas
from src.platform.runtime.native.sampling.registry import sampler_registry, schedule_registry

PLUGIN_CODE = '''
import torch


def sample_frozen(model_fn, x, sigmas, guidance, cond, uncond, hooks=(),
                  is_cancelled=None, sampler_options=None):
    """Uniform sampler signature; returns a marker the test can recognise."""
    return torch.full_like(x, 7.0)


def build_squared(ctx):
    """Descending 1.0 -> 0.0 ramp, squared, so it is visibly not the identity."""
    return torch.linspace(1.0, 0.0, ctx.steps + 1, dtype=torch.float32) ** 2
'''


class TestPluginSamplerScheduleRegistration(unittest.TestCase):
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
            sampler_registry.unregister_source(plugin_id)
            schedule_registry.unregister_source(plugin_id)
        shutil.rmtree(self.temp_dir)

    def _create_plugin(self, plugin_id, samplers=None, schedules=None):
        self.enabled.append(plugin_id)
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()

        manifest_data = {
            'id': plugin_id,
            'name': plugin_id,
            'version': '1.0.0',
            'description': 'Test plugin',
            'author': 'Test Author',
            'type': 'backend-only',
            'samplers': samplers or [],
            'schedules': schedules or [],
        }
        with open(plugin_dir / "manifest.yml", 'w') as f:
            yaml.dump(manifest_data, f)
        (plugin_dir / "sampling.py").write_text(PLUGIN_CODE)
        return plugin_dir

    def _create_full_plugin(self, plugin_id, sampler_key='frozen', schedule_key='squared'):
        return self._create_plugin(
            plugin_id,
            samplers=[{
                'key': sampler_key,
                'handler': 'sampling.sample_frozen',
                'label': 'Frozen',
                'stochastic': True,
                'description': 'Returns a constant.',
                'options': [{
                    'name': 'eta', 'type': 'float', 'default': 0.5,
                    'description': 'Noise fraction', 'min_value': 0.0, 'max_value': 1.0,
                }],
            }],
            schedules=[{
                'key': schedule_key,
                'handler': 'sampling.build_squared',
                'label': 'Squared',
                'families': ['ltx'],
                'description': 'A test ramp.',
            }],
        )

    def test_enable_registers_both_with_the_plugin_as_source(self):
        self._create_full_plugin('sampling-plugin')

        self.assertTrue(self.registry.enable_plugin('sampling-plugin'))

        sampler = sampler_registry.get('frozen')
        self.assertEqual(sampler.source, 'sampling-plugin')
        self.assertEqual(sampler.label, 'Frozen')
        self.assertTrue(sampler.stochastic)
        self.assertEqual(sampler.families, ('*',))
        self.assertEqual([o.name for o in sampler.options], ['eta'])
        self.assertEqual(sampler.options[0].max_value, 1.0)

        schedule = schedule_registry.get('squared')
        self.assertEqual(schedule.source, 'sampling-plugin')
        self.assertEqual(schedule.families, ('ltx',))
        self.assertFalse(schedule.owns_steps)

    def test_denoise_runs_the_plugin_sampler(self):
        self._create_full_plugin('sampling-plugin-run')
        self.assertTrue(self.registry.enable_plugin('sampling-plugin-run'))

        latents = torch.zeros(1, 4, 2, 2)
        out = denoise(
            lambda x, sigma, cond: torch.zeros_like(x),
            latents, cond={}, steps=4, sampler_name='frozen',
            sampling_settings={'shift': 1.0, 'guidance': None}, guidance_scale=0.0,
            seed_noise=torch.zeros_like(latents),
        )
        self.assertTrue(torch.allclose(out, torch.full_like(latents, 7.0)))

    def test_build_sigmas_dispatches_to_the_plugin_schedule(self):
        self._create_full_plugin('sampling-plugin-sigmas')
        self.assertTrue(self.registry.enable_plugin('sampling-plugin-sigmas'))

        sigmas = build_sigmas(6, schedule='squared')
        self.assertEqual(tuple(sigmas.shape), (7,))
        self.assertAlmostEqual(sigmas[0].item(), 1.0, places=5)
        self.assertEqual(sigmas[-1].item(), 0.0)
        # The plugin's own curve, not the shift ramp it replaced.
        self.assertFalse(torch.allclose(sigmas, build_sigmas(6, shift=1.0)))

    def test_disable_removes_both_and_build_sigmas_rejects_the_key(self):
        self._create_full_plugin('sampling-plugin-off')
        self.assertTrue(self.registry.enable_plugin('sampling-plugin-off'))
        self.assertTrue(sampler_registry.has('frozen'))
        self.assertTrue(schedule_registry.has('squared'))

        self.assertTrue(self.registry.disable_plugin('sampling-plugin-off'))

        self.assertFalse(sampler_registry.has('frozen'))
        self.assertFalse(schedule_registry.has('squared'))
        with self.assertRaises(ValueError) as ctx:
            build_sigmas(6, schedule='squared')
        self.assertIn('squared', str(ctx.exception))
        with self.assertRaises(ValueError):
            denoise(
                lambda x, sigma, cond: torch.zeros_like(x),
                torch.zeros(1, 4, 2, 2), cond={}, steps=4, sampler_name='frozen',
                sampling_settings={'shift': 1.0, 'guidance': None}, guidance_scale=0.0,
            )

    def test_enable_fails_when_a_sampler_key_collides_with_core(self):
        self._create_plugin('colliding-sampler-plugin', samplers=[
            {'key': 'euler', 'handler': 'sampling.sample_frozen', 'label': 'Mine'}
        ])

        self.assertFalse(self.registry.enable_plugin('colliding-sampler-plugin'))
        self.assertEqual(
            self.registry.get_plugin_state('colliding-sampler-plugin'), PluginState.ERROR
        )
        error = self.registry.get_plugin_error('colliding-sampler-plugin')
        self.assertIn('euler', error)
        self.assertIn('core', error)
        # Core's own algorithm is untouched by the refused registration.
        self.assertEqual(sampler_registry.get('euler').source, 'core')

    def test_enable_fails_when_a_schedule_key_collides_with_core(self):
        self._create_plugin('colliding-schedule-plugin', schedules=[
            {'key': 'beta', 'handler': 'sampling.build_squared', 'label': 'Mine'}
        ])

        self.assertFalse(self.registry.enable_plugin('colliding-schedule-plugin'))
        error = self.registry.get_plugin_error('colliding-schedule-plugin')
        self.assertIn('beta', error)
        self.assertEqual(schedule_registry.get('beta').source, 'core')

    def test_enable_fails_when_the_handler_cannot_be_loaded(self):
        self._create_plugin('broken-handler-plugin', samplers=[
            {'key': 'ghost', 'handler': 'sampling.no_such_function', 'label': 'Ghost'}
        ])

        self.assertFalse(self.registry.enable_plugin('broken-handler-plugin'))
        self.assertIn(
            'sampling.no_such_function',
            self.registry.get_plugin_error('broken-handler-plugin'),
        )
        self.assertFalse(sampler_registry.has('ghost'))

    def test_a_failed_schedule_stage_rolls_back_the_samplers_already_registered(self):
        self._create_plugin('half-broken-plugin',
            samplers=[{'key': 'lands_first', 'handler': 'sampling.sample_frozen', 'label': 'A'}],
            schedules=[{'key': 'manual', 'handler': 'sampling.build_squared', 'label': 'B'}],
        )

        self.assertFalse(self.registry.enable_plugin('half-broken-plugin'))
        self.assertFalse(sampler_registry.has('lands_first'))
        self.assertEqual(schedule_registry.get('manual').source, 'core')


if __name__ == '__main__':
    unittest.main()
