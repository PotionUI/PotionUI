"""Tests for the Remote Native handshake fingerprints.

Covers determinism (same value from the same registry state, including across
a fresh interpreter with a different hash seed) and sensitivity (a changed
pipe/plugin moves the value, an irrelevant one does not).
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

from src.pipelines.catalog import PipeCatalog
from src.pipelines.contracts import (
    BasePipe,
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
)
from src.pipelines.remote_fingerprint import (
    compute_build_fingerprint,
    compute_pipe_catalog_fingerprint,
    compute_remote_plugin_bundle_fingerprint,
)
from src.platform.plugins.loader import PluginManifest
from src.platform.worker_protocol.version import WORKER_PROTOCOL_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]


class SimplePipe(BasePipe):
    name = "simple_pipe"
    description = "A pipe used only to exercise the fingerprint"

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        return PipeOutput(output={})

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {"steps": 20}

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [PipeInputSpec(name="image", io_type=IOType.IMAGE, required=True)]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec(name="image", io_type=IOType.IMAGE)]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [PipeConfigSpec(name="steps", param_type=int, default=20, min_value=1, max_value=100)]


def _catalog_with_pipes(pipes: Dict[str, type], sources: Dict[str, str] = None) -> PipeCatalog:
    """A PipeCatalog pre-loaded with `pipes` without touching the filesystem -
    `_discovered=True` short-circuits `get_available_pipes()`'s discovery."""
    catalog = PipeCatalog("unused_core", "unused_custom")
    catalog._discovered = True
    catalog._light_discovered = True
    catalog.pipes = dict(pipes)
    catalog.pipe_sources = dict(sources) if sources is not None else {k: "core" for k in pipes}
    return catalog


def _manifest(
    plugin_id: str, version: str = "1.0.0", deps_python=(), deps_binaries=(), remote_hooks=()
) -> PluginManifest:
    return PluginManifest(
        id=plugin_id,
        name=plugin_id,
        version=version,
        description="",
        author="test",
        plugin_type="integration",
        dependencies_python=list(deps_python),
        dependencies_binaries=list(deps_binaries),
        remote_hooks=list(remote_hooks),
    )


class TestPipeCatalogFingerprint(unittest.TestCase):
    def test_deterministic_same_process(self):
        catalog = _catalog_with_pipes({"simple_pipe": SimplePipe})
        first = compute_pipe_catalog_fingerprint(catalog)
        second = compute_pipe_catalog_fingerprint(catalog)
        self.assertEqual(first, second)

    def test_changes_when_input_spec_changes(self):
        class ChangedInputsPipe(SimplePipe):
            @classmethod
            def inputs(cls) -> List[PipeInputSpec]:
                return [PipeInputSpec(name="image", io_type=IOType.IMAGE, required=False)]

        baseline = compute_pipe_catalog_fingerprint(_catalog_with_pipes({"simple_pipe": SimplePipe}))
        changed = compute_pipe_catalog_fingerprint(_catalog_with_pipes({"simple_pipe": ChangedInputsPipe}))
        self.assertNotEqual(baseline, changed)

    def test_changes_when_config_schema_changes(self):
        class ChangedConfigPipe(SimplePipe):
            @classmethod
            def configuration(cls) -> List[PipeConfigSpec]:
                return [PipeConfigSpec(name="steps", param_type=int, default=20, min_value=1, max_value=999)]

        baseline = compute_pipe_catalog_fingerprint(_catalog_with_pipes({"simple_pipe": SimplePipe}))
        changed = compute_pipe_catalog_fingerprint(_catalog_with_pipes({"simple_pipe": ChangedConfigPipe}))
        self.assertNotEqual(baseline, changed)

    def test_unchanged_by_description_only_change(self):
        class RewordedPipe(SimplePipe):
            description = "A totally different description, same contract"

        baseline = compute_pipe_catalog_fingerprint(_catalog_with_pipes({"simple_pipe": SimplePipe}))
        reworded = compute_pipe_catalog_fingerprint(_catalog_with_pipes({"simple_pipe": RewordedPipe}))
        self.assertEqual(baseline, reworded)

    def test_changes_when_pipe_added(self):
        class OtherPipe(SimplePipe):
            name = "other_pipe"

        baseline = compute_pipe_catalog_fingerprint(_catalog_with_pipes({"simple_pipe": SimplePipe}))
        with_extra = compute_pipe_catalog_fingerprint(
            _catalog_with_pipes({"simple_pipe": SimplePipe, "other_pipe": OtherPipe})
        )
        self.assertNotEqual(baseline, with_extra)

    def test_independent_of_dict_insertion_order(self):
        """`catalog.pipes` is populated off `os.listdir()` (core/custom) and
        plugin discovery order, neither of which is guaranteed stable across
        machines or repeated runs - the digest must not care which order the
        dict was built in, only its content."""
        class OtherPipe(SimplePipe):
            name = "other_pipe"

        forward = compute_pipe_catalog_fingerprint(
            _catalog_with_pipes({"simple_pipe": SimplePipe, "other_pipe": OtherPipe})
        )
        reversed_order = compute_pipe_catalog_fingerprint(
            _catalog_with_pipes({"other_pipe": OtherPipe, "simple_pipe": SimplePipe})
        )
        self.assertEqual(forward, reversed_order)

    def test_changes_when_pipe_removed(self):
        class OtherPipe(SimplePipe):
            name = "other_pipe"

        with_both = compute_pipe_catalog_fingerprint(
            _catalog_with_pipes({"simple_pipe": SimplePipe, "other_pipe": OtherPipe})
        )
        without_other = compute_pipe_catalog_fingerprint(_catalog_with_pipes({"simple_pipe": SimplePipe}))
        self.assertNotEqual(with_both, without_other)

    def test_stable_across_fresh_interpreter_with_different_hash_seed(self):
        """The honest version of a determinism test: two separate interpreter
        processes, each started with a different PYTHONHASHSEED, must compute
        the same digest for the same catalog contents."""
        script = f"""
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
from src.pipelines.catalog import PipeCatalog
from src.pipelines.contracts import BasePipe, IOType, PipeConfigSpec, PipeInput, PipeInputSpec, PipeOutput, PipeOutputSpec
from src.pipelines.remote_fingerprint import compute_pipe_catalog_fingerprint

class P(BasePipe):
    name = "simple_pipe"
    description = "x"
    def process(self, pipe_input, generation_outputs):
        return PipeOutput(output={{}})
    @classmethod
    def get_default_config(cls):
        return {{"steps": 20}}
    @classmethod
    def inputs(cls):
        return [PipeInputSpec(name="image", io_type=IOType.IMAGE, required=True)]
    @classmethod
    def outputs(cls):
        return [PipeOutputSpec(name="image", io_type=IOType.IMAGE)]
    @classmethod
    def configuration(cls):
        return [PipeConfigSpec(name="steps", param_type=int, default=20, min_value=1, max_value=100)]

catalog = PipeCatalog("unused_core", "unused_custom")
catalog._discovered = True
catalog._light_discovered = True
catalog.pipes = {{"simple_pipe": P}}
catalog.pipe_sources = {{"simple_pipe": "core"}}
print(compute_pipe_catalog_fingerprint(catalog))
"""
        digests = set()
        for seed in ("0", "1234"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            result = subprocess.run(
                [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            digests.add(result.stdout.strip())
        self.assertEqual(len(digests), 1, f"digest varied across hash seeds: {digests}")


class TestRemotePluginBundleFingerprint(unittest.TestCase):
    def test_deterministic_same_process(self):
        catalog = _catalog_with_pipes({"p": SimplePipe}, sources={"p": "plug-a"})
        manifests = [_manifest("plug-a")]
        first = compute_remote_plugin_bundle_fingerprint(catalog, manifests)
        second = compute_remote_plugin_bundle_fingerprint(catalog, manifests)
        self.assertEqual(first, second)

    def test_excludes_plugin_that_contributes_no_pipe(self):
        """Not every enabled plugin is remote-relevant: a plugin with zero
        pipe-sourced entries in the catalog must not affect the fingerprint."""
        catalog = _catalog_with_pipes({"p": SimplePipe}, sources={"p": "plug-a"})
        baseline = compute_remote_plugin_bundle_fingerprint(catalog, [_manifest("plug-a")])
        with_extra_plugin = compute_remote_plugin_bundle_fingerprint(
            catalog, [_manifest("plug-a"), _manifest("plug-b-frontend-only")]
        )
        self.assertEqual(baseline, with_extra_plugin)

    def test_changes_when_contributing_plugin_version_changes(self):
        catalog = _catalog_with_pipes({"p": SimplePipe}, sources={"p": "plug-a"})
        v1 = compute_remote_plugin_bundle_fingerprint(catalog, [_manifest("plug-a", version="1.0.0")])
        v2 = compute_remote_plugin_bundle_fingerprint(catalog, [_manifest("plug-a", version="1.0.1")])
        self.assertNotEqual(v1, v2)

    def test_changes_when_contributing_plugin_dependencies_change(self):
        catalog = _catalog_with_pipes({"p": SimplePipe}, sources={"p": "plug-a"})
        without_dep = compute_remote_plugin_bundle_fingerprint(catalog, [_manifest("plug-a")])
        with_dep = compute_remote_plugin_bundle_fingerprint(
            catalog, [_manifest("plug-a", deps_python=["torch>=2.0"])]
        )
        self.assertNotEqual(without_dep, with_dep)

    def test_excludes_core_and_custom_sources(self):
        catalog = _catalog_with_pipes(
            {"a": SimplePipe, "b": SimplePipe}, sources={"a": "core", "b": "custom"}
        )
        digest = compute_remote_plugin_bundle_fingerprint(catalog, [])
        # No plugin-sourced pipes at all -> empty bundle, independent of which
        # core/custom pipes exist.
        catalog_more_core = _catalog_with_pipes(
            {"a": SimplePipe, "b": SimplePipe, "c": SimplePipe},
            sources={"a": "core", "b": "custom", "c": "core"},
        )
        digest_more = compute_remote_plugin_bundle_fingerprint(catalog_more_core, [])
        self.assertEqual(digest, digest_more)

    def test_independent_of_enabled_plugins_list_order(self):
        catalog = _catalog_with_pipes(
            {"a": SimplePipe, "b": SimplePipe}, sources={"a": "plug-a", "b": "plug-b"}
        )
        forward = compute_remote_plugin_bundle_fingerprint(
            catalog, [_manifest("plug-a"), _manifest("plug-b")]
        )
        reversed_order = compute_remote_plugin_bundle_fingerprint(
            catalog, [_manifest("plug-b"), _manifest("plug-a")]
        )
        self.assertEqual(forward, reversed_order)

    def test_raises_on_stale_enabled_plugins_list(self):
        catalog = _catalog_with_pipes({"p": SimplePipe}, sources={"p": "plug-a"})
        with self.assertRaises(ValueError):
            compute_remote_plugin_bundle_fingerprint(catalog, [])  # plug-a missing

    def test_hook_only_plugin_with_remote_flag_is_included(self):
        """A plugin that contributes no pipe but declares a `remote: true`
        backend hook must still be visible to the fingerprint - that is the
        whole point of the `remote_hooks` union."""
        catalog = _catalog_with_pipes({"p": SimplePipe}, sources={"p": "plug-a"})
        without_hook_plugin = compute_remote_plugin_bundle_fingerprint(
            catalog, [_manifest("plug-a")]
        )
        with_hook_plugin = compute_remote_plugin_bundle_fingerprint(
            catalog,
            [_manifest("plug-a"), _manifest("hook-plugin", remote_hooks=["prompt.transform:mod.fn"])],
        )
        self.assertNotEqual(without_hook_plugin, with_hook_plugin)

    def test_hook_only_plugin_remote_flag_version_change_moves_fingerprint(self):
        catalog = _catalog_with_pipes({"p": SimplePipe}, sources={"p": "plug-a"})
        v1 = compute_remote_plugin_bundle_fingerprint(
            catalog,
            [_manifest("plug-a"), _manifest("hook-plugin", version="1.0.0", remote_hooks=["h:mod.fn"])],
        )
        v2 = compute_remote_plugin_bundle_fingerprint(
            catalog,
            [_manifest("plug-a"), _manifest("hook-plugin", version="1.0.1", remote_hooks=["h:mod.fn"])],
        )
        self.assertNotEqual(v1, v2)

    def test_hook_only_plugin_without_remote_flag_is_excluded(self):
        """A hook-only plugin whose hooks are all core-only (no `remote: true`
        entries, i.e. `manifest.remote_hooks` is empty) must not affect the
        fingerprint, same as any other plugin that contributes nothing
        remote-relevant."""
        catalog = _catalog_with_pipes({"p": SimplePipe}, sources={"p": "plug-a"})
        baseline = compute_remote_plugin_bundle_fingerprint(catalog, [_manifest("plug-a")])
        with_core_only_hook_plugin = compute_remote_plugin_bundle_fingerprint(
            catalog, [_manifest("plug-a"), _manifest("core-hook-plugin")]
        )
        self.assertEqual(baseline, with_core_only_hook_plugin)

    def test_pipe_contributing_plugin_adding_remote_hook_moves_fingerprint(self):
        """A plugin that already contributes a pipe and then also declares a
        `remote: true` hook must move the fingerprint - the hook is a second,
        additive reason the worker needs to know about it."""
        catalog = _catalog_with_pipes({"p": SimplePipe}, sources={"p": "plug-a"})
        without_hook = compute_remote_plugin_bundle_fingerprint(catalog, [_manifest("plug-a")])
        with_hook = compute_remote_plugin_bundle_fingerprint(
            catalog, [_manifest("plug-a", remote_hooks=["prompt.transform:mod.fn"])]
        )
        self.assertNotEqual(without_hook, with_hook)

    def test_stable_across_fresh_interpreter_with_different_hash_seed(self):
        """`remote_relevant_plugin_ids()` returns a `set` - the one real source
        of hash-seed-dependent iteration order in this whole module - so this
        is the fingerprint that actually exercises the claim, unlike the pipe
        catalog one where a single-pipe dict has no order to leak."""
        script = f"""
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
from src.pipelines.catalog import PipeCatalog
from src.pipelines.contracts import BasePipe, IOType, PipeConfigSpec, PipeInput, PipeInputSpec, PipeOutput, PipeOutputSpec
from src.pipelines.remote_fingerprint import compute_remote_plugin_bundle_fingerprint
from src.platform.plugins.loader import PluginManifest

class P(BasePipe):
    name = "p"
    description = "x"
    def process(self, pipe_input, generation_outputs):
        return PipeOutput(output={{}})
    @classmethod
    def get_default_config(cls):
        return {{}}
    @classmethod
    def inputs(cls):
        return []
    @classmethod
    def outputs(cls):
        return []
    @classmethod
    def configuration(cls):
        return []

catalog = PipeCatalog("unused_core", "unused_custom")
catalog._discovered = True
catalog._light_discovered = True
catalog.pipes = {{"a": P, "b": P, "c": P, "d": P, "e": P, "f": P}}
catalog.pipe_sources = {{"a": "plug-a", "b": "plug-b", "c": "plug-c", "d": "plug-d", "e": "plug-e", "f": "plug-f"}}
manifests = [
    PluginManifest(id=pid, name=pid, version="1.0.0", description="", author="t", plugin_type="integration")
    for pid in ("plug-a", "plug-b", "plug-c", "plug-d", "plug-e", "plug-f")
]
print(compute_remote_plugin_bundle_fingerprint(catalog, manifests))
"""
        digests = set()
        for seed in ("0", "1234"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            result = subprocess.run(
                [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            digests.add(result.stdout.strip())
        self.assertEqual(len(digests), 1, f"digest varied across hash seeds: {digests}")


class TestBuildFingerprint(unittest.TestCase):
    def test_deterministic_same_process(self):
        self.assertEqual(compute_build_fingerprint(), compute_build_fingerprint())

    def test_changes_with_build_id(self):
        self.assertNotEqual(compute_build_fingerprint(None), compute_build_fingerprint("abc123"))

    def test_changes_when_protocol_version_bumped(self):
        """`compute_build_fingerprint` reads `WORKER_PROTOCOL_VERSION` as a
        module global at call time, not a value captured at import time - so
        patching it on `remote_fingerprint`'s own namespace (where the `from
        ... import` bound it) must move the digest, proving the constant isn't
        baked in as a stale default anywhere along the way."""
        import src.pipelines.remote_fingerprint as module

        baseline = compute_build_fingerprint()
        original = module.WORKER_PROTOCOL_VERSION
        try:
            module.WORKER_PROTOCOL_VERSION = original + 1
            bumped = compute_build_fingerprint()
        finally:
            module.WORKER_PROTOCOL_VERSION = original
        self.assertNotEqual(baseline, bumped)

    def test_protocol_version_is_a_positive_int(self):
        self.assertIsInstance(WORKER_PROTOCOL_VERSION, int)
        self.assertGreaterEqual(WORKER_PROTOCOL_VERSION, 1)


if __name__ == "__main__":
    unittest.main()
