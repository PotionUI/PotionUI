"""`plugin.lifecycle.boot` - the per-process lifecycle hook.

`plugin.lifecycle.enable` fires only on the disabled->enabled transition, so a
plugin that does boot-time initialization there silently stops being
initialized after the first restart. `boot` is the hook that fires on every
process start for every enabled plugin, and these tests pin that split.

Handlers are written to disk as real plugin modules and record what ran by
appending to a log file (the plugin loader imports them in its own module
namespace, so an in-process list wouldn't be shared).
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from src.platform.plugins.lifecycle_hooks import PLUGIN_LIFECYCLE_HOOKS
from src.platform.plugins.registry import PluginRegistry


class TestPluginBootHook(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()
        self.log_file = self.temp_dir / "lifecycle.log"

        self.registry = PluginRegistry(str(self.marketplace_dir), str(self.local_dir))

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _create_plugin(self, plugin_id: str, *, hooks: dict, failing: tuple = ()):
        """Write a plugin whose handlers append '<plugin_id>:<event>' to the log.

        `hooks` maps a lifecycle event name ("enable"/"boot"/"disable") to the
        handler function name; anything named in `failing` raises instead of
        returning, after recording its attempt.
        """
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()

        manifest = {
            'id': plugin_id,
            'name': plugin_id,
            'version': '1.0.0',
            'description': f'{plugin_id} lifecycle fixture',
            'author': 'Test Author',
            'type': 'full-stack',
            'hooks': {
                'backend': [
                    {'hook': f'plugin.lifecycle.{event}', 'handler': f'hooks.{func}'}
                    for event, func in hooks.items()
                ]
            },
        }
        (plugin_dir / "manifest.yml").write_text(yaml.dump(manifest))

        body = [f"LOG = {str(self.log_file)!r}", ""]
        for event, func in hooks.items():
            body.append(f"def {func}(context):")
            body.append("    with open(LOG, 'a') as fh:")
            body.append(f"        fh.write('{plugin_id}:{event}\\n')")
            if event in failing:
                body.append(f"    raise RuntimeError('{plugin_id} {event} handler exploded')")
            body.append("    return context")
            body.append("")
        (plugin_dir / "hooks.py").write_text("\n".join(body))

        return plugin_dir

    def _events(self):
        if not self.log_file.exists():
            return []
        return self.log_file.read_text().split()

    def test_boot_fires_for_every_enabled_plugin(self):
        self._create_plugin("plugin-a", hooks={'boot': 'on_boot'})
        self._create_plugin("plugin-b", hooks={'boot': 'on_boot'})

        self.assertTrue(self.registry.enable_plugin("plugin-a"))
        self.assertTrue(self.registry.enable_plugin("plugin-b"))
        self.registry.run_boot_hooks()

        self.assertEqual(sorted(self._events()), ["plugin-a:boot", "plugin-b:boot"])

    def test_boot_does_not_fire_for_a_plugin_that_was_never_enabled(self):
        self._create_plugin("enabled-plugin", hooks={'boot': 'on_boot'})
        self._create_plugin("dormant-plugin", hooks={'boot': 'on_boot'})

        self.assertTrue(self.registry.enable_plugin("enabled-plugin"))
        self.registry.run_boot_hooks()

        self.assertEqual(self._events(), ["enabled-plugin:boot"])

    def test_boot_does_not_fire_for_a_disabled_plugin(self):
        self._create_plugin("toggled-plugin", hooks={'boot': 'on_boot'})

        self.assertTrue(self.registry.enable_plugin("toggled-plugin"))
        self.assertTrue(self.registry.disable_plugin("toggled-plugin"))
        self.registry.run_boot_hooks()

        self.assertEqual(self._events(), [])

    def test_registry_enable_registers_handlers_without_running_the_enable_chain(self):
        """Registering a plugin's handlers is not the same as firing its
        lifecycle chain - the boot path must produce `boot` and nothing else."""
        self._create_plugin("both-hooks", hooks={'enable': 'on_enable', 'boot': 'on_boot'})

        self.assertTrue(self.registry.enable_plugin("both-hooks"))
        self.registry.run_boot_hooks()

        self.assertEqual(self._events(), ["both-hooks:boot"])

    def test_boot_is_dispatched_only_to_the_subject_plugins_own_handler(self):
        self._create_plugin("subject-plugin", hooks={'boot': 'on_boot'})
        self._create_plugin("bystander-plugin", hooks={'boot': 'on_boot'})

        self.assertTrue(self.registry.enable_plugin("subject-plugin"))
        self.assertTrue(self.registry.enable_plugin("bystander-plugin"))
        self.registry.run_boot_hook("subject-plugin")

        self.assertEqual(self._events(), ["subject-plugin:boot"])

    def test_a_failing_boot_handler_does_not_stop_the_next_plugin(self):
        self._create_plugin("aaa-broken", hooks={'boot': 'on_boot'}, failing=('boot',))
        self._create_plugin("bbb-healthy", hooks={'boot': 'on_boot'})

        self.assertTrue(self.registry.enable_plugin("aaa-broken"))
        self.assertTrue(self.registry.enable_plugin("bbb-healthy"))
        self.registry.run_boot_hooks()

        self.assertEqual(sorted(self._events()), ["aaa-broken:boot", "bbb-healthy:boot"])
        self.assertIsNone(self.registry.get_plugin_error("aaa-broken"))

    def test_run_boot_hook_for_a_plugin_without_a_boot_handler_is_a_no_op(self):
        self._create_plugin("enable-only", hooks={'enable': 'on_enable'})

        self.assertTrue(self.registry.enable_plugin("enable-only"))
        self.registry.run_boot_hook("enable-only")
        self.registry.run_boot_hook("no-such-plugin")

        self.assertEqual(self._events(), [])

    def test_boot_payload_carries_the_subject_plugin_id(self):
        recorded = []
        self.registry.hook_chain.register(
            PLUGIN_LIFECYCLE_HOOKS.boot,
            "recording-plugin",
            lambda ctx: recorded.append(ctx.get("plugin_id")) or ctx,
        )

        self.registry.run_boot_hook("recording-plugin")

        self.assertEqual(recorded, ["recording-plugin"])


if __name__ == '__main__':
    unittest.main()
