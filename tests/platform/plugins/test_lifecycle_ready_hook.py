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

ROOT = Path(__file__).resolve().parents[3]


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

    def test_the_lifespan_fires_the_hook_once_right_after_the_reconciliation(self):
        tree = ast.parse((ROOT / "src/bootstrap/app.py").read_text())
        lifespan = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")
        calls = [
            (n.lineno, n.func.attr)
            for n in ast.walk(lifespan)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        ]
        names = [name for _, name in sorted(calls)]

        self.assertEqual(names.count("run_ready_hooks"), 1)
        reconcile = names.index("reconcile_interrupted_generations")
        self.assertLess(reconcile, names.index("run_ready_hooks"))
        between = names[reconcile + 1:names.index("run_ready_hooks")]
        self.assertEqual([n for n in between if n not in ("info", "get_running_loop")], [])


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
