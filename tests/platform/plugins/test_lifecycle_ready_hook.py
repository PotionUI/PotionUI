import ast
import asyncio
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from src.platform.plugins import runtime_registries
from src.platform.plugins.hooks import hooks_registry
from src.platform.plugins.registry import PluginRegistry
from tests.bootstrap.lifespan_support import lifespan


class TestPluginReadyHook(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()
        self.log_file = self.temp_dir / "ready.log"
        self.registry = PluginRegistry(str(self.marketplace_dir), str(self.local_dir))

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _create_plugin(self, plugin_id, *, events=("ready",), failing=False):
        plugin_dir = self.marketplace_dir / plugin_id
        plugin_dir.mkdir()
        manifest = {
            'id': plugin_id,
            'name': plugin_id,
            'version': '1.0.0',
            'description': f'{plugin_id} ready fixture',
            'author': 'Test Author',
            'type': 'full-stack',
            'hooks': {
                'backend': [
                    {'hook': f'plugin.lifecycle.{event}', 'handler': f'hooks.on_{event}'}
                    for event in events
                ]
            },
        }
        (plugin_dir / "manifest.yml").write_text(yaml.dump(manifest))
        body = [f"LOG = {str(self.log_file)!r}", ""]
        for event in events:
            body.append(f"def on_{event}(context):")
            body.append("    with open(LOG, 'a') as fh:")
            body.append(f"        fh.write('{plugin_id}:{event}:' + str(context.get('plugin_id')) + '\\n')")
            if failing:
                body.append("    raise RuntimeError('exploded')")
            body.append("    return context")
            body.append("")
        (plugin_dir / "hooks.py").write_text("\n".join(body))

    def _events(self):
        if not self.log_file.exists():
            return []
        return self.log_file.read_text().split()

    def test_the_hook_is_in_the_catalog_with_its_payload(self):
        spec = next(s for s in hooks_registry.all() if s.name == 'plugin.lifecycle.ready')

        self.assertEqual(spec.type, 'backend')
        self.assertEqual(set(spec.payload), {'plugin_id'})

    def test_a_plugin_handler_receives_its_own_id(self):
        self._create_plugin("plugin-a")
        self.assertTrue(self.registry.enable_plugin("plugin-a"))

        self.registry.run_ready_hooks()

        self.assertEqual(self._events(), ["plugin-a:ready:plugin-a"])

    def test_each_enabled_plugin_is_notified_once_and_only_about_itself(self):
        self._create_plugin("plugin-a")
        self._create_plugin("plugin-b")
        self._create_plugin("plugin-dormant")
        self.assertTrue(self.registry.enable_plugin("plugin-a"))
        self.assertTrue(self.registry.enable_plugin("plugin-b"))

        self.registry.run_ready_hooks()

        self.assertEqual(sorted(self._events()), ["plugin-a:ready:plugin-a", "plugin-b:ready:plugin-b"])

    def test_the_hook_does_not_fire_for_enable_or_boot(self):
        self._create_plugin("plugin-a")
        self.assertTrue(self.registry.enable_plugin("plugin-a"))

        self.registry.run_boot_hooks()

        self.assertEqual(self._events(), [])

    def test_a_failing_handler_does_not_stop_the_next_plugin(self):
        self._create_plugin("aaa-broken", failing=True)
        self._create_plugin("bbb-healthy")
        self.assertTrue(self.registry.enable_plugin("aaa-broken"))
        self.assertTrue(self.registry.enable_plugin("bbb-healthy"))

        self.registry.run_ready_hooks()

        self.assertEqual(sorted(self._events()), ["aaa-broken:ready:aaa-broken", "bbb-healthy:ready:bbb-healthy"])

    def test_a_plugin_enabled_after_startup_is_notified_once(self):
        self._create_plugin("plugin-a")
        self._create_plugin("plugin-late")
        self.assertTrue(self.registry.enable_plugin("plugin-a"))
        self.registry.run_ready_hooks()
        self.assertTrue(self.registry.enable_plugin("plugin-late"))

        self.registry.run_ready_hook("plugin-late")
        self.registry.run_ready_hook("plugin-late")
        self.registry.run_ready_hook("plugin-a")

        self.assertEqual(sorted(self._events()), ["plugin-a:ready:plugin-a", "plugin-late:ready:plugin-late"])

    def test_a_single_ready_hook_waits_for_the_startup_phase(self):
        self._create_plugin("plugin-a")
        self.assertTrue(self.registry.enable_plugin("plugin-a"))

        self.registry.run_ready_hook("plugin-a")
        self.assertEqual(self._events(), [])

        self.registry.run_ready_hooks()
        self.assertEqual(self._events(), ["plugin-a:ready:plugin-a"])

    def test_the_lifespan_fires_the_hook_once_right_after_the_reconciliation(self):
        calls = [
            (n.lineno, n.func.attr)
            for n in ast.walk(lifespan())
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        ]
        names = [name for _, name in sorted(calls)]

        self.assertEqual(names.count("run_ready_hooks"), 1)
        reconcile = names.index("reconcile_interrupted_generations")
        self.assertLess(reconcile, names.index("run_ready_hooks"))
        between = names[reconcile + 1:names.index("run_ready_hooks")]
        self.assertEqual([n for n in between if n not in ("info", "error", "get_running_loop", "reconcile_interrupted")], [])


class TestPluginTeardownDisableHook(unittest.TestCase):

    _create_plugin = TestPluginReadyHook._create_plugin
    _events = TestPluginReadyHook._events

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.marketplace_dir = self.temp_dir / "marketplace"
        self.local_dir = self.temp_dir / "local"
        self.marketplace_dir.mkdir()
        self.local_dir.mkdir()
        self.log_file = self.temp_dir / "ready.log"
        self.registry = PluginRegistry(str(self.marketplace_dir), str(self.local_dir))

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _enabled(self, plugin_id="plugin-a", **kwargs):
        self._create_plugin(plugin_id, events=("ready", "disable"), **kwargs)
        self.registry.discover_plugins()
        self.assertTrue(self.registry.enable_plugin(plugin_id))

    def _bump_manifest(self, plugin_id):
        path = self.marketplace_dir / plugin_id / "manifest.yml"
        manifest = yaml.safe_load(path.read_text())
        manifest["version"] = "2.0.0"
        path.write_text(yaml.dump(manifest))

    def _count(self, event, plugin_id="plugin-a"):
        return self._events().count(f"{plugin_id}:{event}:{plugin_id}")

    def test_disabling_an_enabled_plugin_fires_disable_once(self):
        self._enabled()

        self.assertTrue(self.registry.disable_plugin("plugin-a"))

        self.assertEqual(self._count("disable"), 1)

    def test_disabling_again_or_a_never_enabled_plugin_fires_nothing(self):
        self._create_plugin("plugin-never", events=("ready", "disable"))
        self.registry.discover_plugins()
        self.assertTrue(self.registry.disable_plugin("plugin-never"))
        self._enabled()
        self.registry.disable_plugin("plugin-a")
        self.registry.disable_plugin("plugin-a")

        self.assertEqual(self._count("disable", "plugin-never"), 0)
        self.assertEqual(self._count("disable"), 1)

    def test_a_failed_enable_never_fires_disable(self):
        self._enabled("plugin-b")
        self._create_plugin("plugin-a", events=("ready", "disable"))
        (self.marketplace_dir / "plugin-a" / "hooks.py").write_text("def broken(:\n")
        self.registry.discover_plugins()

        self.assertFalse(self.registry.enable_plugin("plugin-a"))

        self.assertEqual(self._events(), [])

    def test_reload_fires_disable_once_and_the_new_copy_gets_ready_again(self):
        self._enabled()
        self.registry.run_ready_hooks()

        self.assertTrue(self.registry.reload_plugin("plugin-a"))

        self.assertEqual(self._count("disable"), 1)
        self.assertEqual(self._count("ready"), 2)

    def test_reload_runs_boot_then_ready_after_the_re_enable(self):
        self._enabled()
        self.registry.run_ready_hooks()
        order = []
        real_boot, real_ready = self.registry.run_boot_hook, self.registry.run_ready_hook
        with patch.object(self.registry, "run_boot_hook", side_effect=lambda pid: (order.append("boot"), real_boot(pid))), \
                patch.object(self.registry, "run_ready_hook", side_effect=lambda pid: (order.append("ready"), real_ready(pid))):
            self.assertTrue(self.registry.reload_plugin("plugin-a"))

        self.assertEqual(order, ["boot", "ready"])

    def test_a_reload_whose_re_enable_fails_fires_disable_once_and_no_boot_or_ready(self):
        self._enabled()
        self.registry.run_ready_hooks()
        (self.marketplace_dir / "plugin-a" / "hooks.py").write_text("def broken(:\n")
        with patch.object(self.registry, "run_boot_hook") as boot, \
                patch.object(self.registry, "run_ready_hook") as ready:
            self.assertFalse(self.registry.reload_plugin("plugin-a"))

        boot.assert_not_called()
        ready.assert_not_called()
        self.assertEqual(self._count("disable"), 1)
        self.assertEqual(self._count("ready"), 1)

    def test_a_changed_manifest_fires_disable_once_and_ready_can_fire_again(self):
        self._enabled()
        self.registry.run_ready_hooks()

        self._bump_manifest("plugin-a")
        changes = self.registry.discover_plugins()
        self.registry.run_ready_hook("plugin-a")

        self.assertEqual(changes.changed, ["plugin-a"])
        self.assertEqual(self._count("disable"), 1)
        self.assertEqual(self._count("ready"), 2)

    def test_an_unchanged_rescan_fires_nothing(self):
        self._enabled()
        self.registry.run_ready_hooks()

        self.registry.discover_plugins()
        self.registry.run_ready_hook("plugin-a")

        self.assertEqual(self._count("disable"), 0)
        self.assertEqual(self._count("ready"), 1)

    def test_a_changed_manifest_of_a_disabled_plugin_fires_nothing_more(self):
        self._enabled()
        self.registry.disable_plugin("plugin-a")

        self._bump_manifest("plugin-a")
        self.registry.discover_plugins()

        self.assertEqual(self._count("disable"), 1)

    def test_a_vanished_plugin_fires_disable_once(self):
        self._enabled()

        shutil.rmtree(self.marketplace_dir / "plugin-a")
        self.registry.discover_plugins()

        self.assertEqual(self._count("disable"), 1)

    def test_a_mount_failure_teardown_fires_disable_once(self):
        self._enabled()

        self.assertTrue(self.registry.fail_enabled_plugin("plugin-a", "mount failed"))

        self.assertEqual(self._count("disable"), 1)

    def test_the_broadcast_reaches_other_plugins(self):
        self._enabled("plugin-a")
        self._enabled("plugin-b")

        self.registry.disable_plugin("plugin-a")

        self.assertEqual(self._count("disable", "plugin-a"), 1)
        self.assertEqual(self._count("disable", "plugin-b"), 0)
        self.assertEqual(self._events().count("plugin-b:disable:plugin-a"), 1)

    def test_a_failing_handler_does_not_abort_the_teardown(self):
        self._enabled(failing=True)

        self.assertTrue(self.registry.disable_plugin("plugin-a"))

        self.assertEqual(self._count("disable"), 1)
        self.assertEqual(self.registry.get_plugins_for_hook("plugin.lifecycle.disable"), [])

    def test_a_handler_calling_back_into_the_registry_does_not_deadlock(self):
        self._enabled()
        self._enabled("plugin-b")
        seen = []

        def handler(context):
            if context.get("plugin_id") == "plugin-a":
                seen.append(self.registry.disable_plugin("plugin-b"))
            return context

        self.registry.hook_chain.register("plugin.lifecycle.disable", "watcher", handler)
        self._bump_manifest("plugin-a")
        worker = threading.Thread(target=self.registry.discover_plugins, daemon=True)
        worker.start()
        worker.join(timeout=10)

        self.assertFalse(worker.is_alive())
        self.assertEqual(seen, [True])

    def test_a_loop_started_on_ready_and_cancelled_on_disable_never_doubles(self):
        self._enabled()
        self.registry.run_ready_hooks()

        def alive():
            events = self._events()
            return events.count("plugin-a:ready:plugin-a") - events.count("plugin-a:disable:plugin-a")

        self.assertEqual(alive(), 1)
        self._bump_manifest("plugin-a")
        self.registry.discover_plugins()
        self.registry.run_ready_hook("plugin-a")
        self.assertEqual(alive(), 1)
        self.registry.reload_plugin("plugin-a")
        self.assertEqual(alive(), 1)


class TestAppLoopAccess(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()

    def _container(self, loop):
        return type("Container", (), {"app_loop": loop})()

    def test_run_on_app_loop_runs_the_coroutine_on_the_app_loop_thread(self):
        async def where():
            return threading.get_ident(), asyncio.get_running_loop()

        with patch.object(runtime_registries, "_container", self._container(self.loop)):
            thread_id, running_loop = runtime_registries.run_on_app_loop(where()).result(timeout=5)

        self.assertEqual(thread_id, self.thread.ident)
        self.assertIs(running_loop, self.loop)

    def test_get_app_loop_returns_the_captured_loop(self):
        with patch.object(runtime_registries, "_container", self._container(self.loop)):
            self.assertIs(runtime_registries.get_app_loop(), self.loop)

    def test_both_raise_before_the_application_has_started(self):
        async def never():
            return 1

        coro = never()
        with patch.object(runtime_registries, "_container", self._container(None)):
            with self.assertRaises(RuntimeError):
                runtime_registries.get_app_loop()
            with self.assertRaises(RuntimeError):
                runtime_registries.run_on_app_loop(coro)

        self.assertIsNone(coro.cr_frame)

    def test_a_closed_loop_is_refused(self):
        async def never():
            return 1

        closed = asyncio.new_event_loop()
        closed.close()
        with patch.object(runtime_registries, "_container", self._container(closed)):
            with self.assertRaises(RuntimeError):
                runtime_registries.run_on_app_loop(never())

    def test_the_plugin_api_exposes_both(self):
        from src.plugin_api import hooks

        self.assertIs(hooks.run_on_app_loop, runtime_registries.run_on_app_loop)
        self.assertIs(hooks.get_app_loop, runtime_registries.get_app_loop)
