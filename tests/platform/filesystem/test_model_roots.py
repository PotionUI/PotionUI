import time
import unittest
from pathlib import Path

from src.platform.filesystem.model_roots import (
    InvalidRelPathError,
    LogicalLocation,
    ModelRoot,
    ModelRootResolver,
    NoWriteRootError,
    RootProbe,
    RootReadOnlyError,
    RootUnavailableError,
    root_path_key,
)


class FakeRepository:

    def __init__(self, roots, bindings):
        self._roots = roots
        self._bindings = bindings

    def list_roots(self):
        return self._roots

    def list_bindings(self):
        return self._bindings


class FakeProbe:

    def __init__(self, states):
        self._states = states

    def state(self, root):
        return self._states.get(root.id, ("online", None))


def _root(root_id, path, *, kind="library", read_only=False, case_insensitive=False, state="online"):
    return {
        "id": root_id,
        "label": root_id,
        "path": str(path),
        "kind": kind,
        "read_only": int(read_only),
        "case_insensitive": int(case_insensitive),
        "state": state,
        "state_reason": None,
    }


def _binding(root_id, model_type, subdir, position, is_write=False):
    return {
        "root_id": root_id,
        "model_type": model_type,
        "subdir": subdir,
        "position": position,
        "is_write": int(is_write),
    }


class TestRootPathKey(unittest.TestCase):

    def test_windows_drive_letter_case_folded(self):
        a = root_path_key(r"C:\Users\X\ComfyUI", os_name="nt")
        b = root_path_key("c:/users/x/comfyui", os_name="nt")
        self.assertEqual(a, b)

    def test_windows_unc_share(self):
        key = root_path_key(r"\\server\share\models", os_name="nt")
        self.assertEqual(key, "//server/share/models")

    def test_windows_long_path_prefix_stripped(self):
        prefixed = root_path_key(r"\\?\D:\ComfyUI\models", os_name="nt")
        plain = root_path_key(r"D:\ComfyUI\models", os_name="nt")
        self.assertEqual(prefixed, plain)

    def test_windows_long_unc_prefix_stripped(self):
        prefixed = root_path_key(r"\\?\UNC\server\share\x", os_name="nt")
        plain = root_path_key(r"\\server\share\x", os_name="nt")
        self.assertEqual(prefixed, plain)

    def test_posix_case_sensitive_by_default(self):
        a = root_path_key("/mnt/Models", os_name="posix")
        b = root_path_key("/mnt/models", os_name="posix")
        self.assertNotEqual(a, b)

    def test_posix_case_insensitive_when_requested(self):
        a = root_path_key("/Volumes/Foo", os_name="posix", case_insensitive=True)
        b = root_path_key("/volumes/foo", os_name="posix", case_insensitive=True)
        self.assertEqual(a, b)

    def test_nfc_nfd_equivalence(self):
        nfc = root_path_key("/mnt/caf\u00e9", os_name="posix")
        nfd = root_path_key("/mnt/cafe\u0301", os_name="posix")
        self.assertEqual(nfc, nfd)


class TestToLogical(unittest.TestCase):

    def test_longest_prefix_wins(self):
        roots = [_root("home", "/data/depot")]
        bindings = [
            _binding("home", "checkpoint", "checkpoints", 0),
            _binding("home", "lora", "checkpoints/nested", 0),
        ]
        resolver = ModelRootResolver(FakeRepository(roots, bindings), FakeProbe({}), Path("/data/depot"))

        loc = resolver.to_logical("/data/depot/checkpoints/nested/x.safetensors")

        self.assertEqual(loc.model_type, "lora")
        self.assertEqual(loc.rel_path, "x.safetensors")

    def test_dot_segments_are_normalized(self):
        roots = [_root("home", "/data/depot")]
        bindings = [_binding("home", "lora", "loras", 0)]
        resolver = ModelRootResolver(FakeRepository(roots, bindings), FakeProbe({}), Path("/data/depot"))

        loc = resolver.to_logical("/data/./depot/loras/./sdxl/x.safetensors")

        self.assertEqual(loc.model_type, "lora")
        self.assertEqual(loc.rel_path, "sdxl/x.safetensors")

    def test_no_match_returns_none(self):
        roots = [_root("home", "/data/depot")]
        bindings = [_binding("home", "lora", "loras", 0)]
        resolver = ModelRootResolver(FakeRepository(roots, bindings), FakeProbe({}), Path("/data/depot"))

        self.assertIsNone(resolver.to_logical("/somewhere/else/x.safetensors"))

    def test_realpath_fallback(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bound = base / "home" / "loras"
            bound.mkdir(parents=True)
            target = bound / "x.safetensors"
            target.write_text("x")

            outside = base / "outside"
            outside.mkdir()
            link = outside / "x.safetensors"
            link.symlink_to(target)

            roots = [_root("home", str(base / "home"))]
            bindings = [_binding("home", "lora", "loras", 0)]
            resolver = ModelRootResolver(FakeRepository(roots, bindings), FakeProbe({}), base)

            loc = resolver.to_logical(str(link))

            self.assertEqual(loc.model_type, "lora")
            self.assertEqual(loc.rel_path, "x.safetensors")


class TestWriteDir(unittest.TestCase):

    def test_no_write_binding_raises(self):
        roots = [_root("home", "/data/depot")]
        bindings = [_binding("home", "lora", "loras", 0, is_write=False)]
        resolver = ModelRootResolver(FakeRepository(roots, bindings), FakeProbe({}), Path("/data/depot"))

        with self.assertRaises(NoWriteRootError):
            resolver.write_dir("lora")

    def test_read_only_root_raises(self):
        roots = [_root("lib", "/data/lib", read_only=True)]
        bindings = [_binding("lib", "lora", "loras", 0, is_write=True)]
        resolver = ModelRootResolver(FakeRepository(roots, bindings), FakeProbe({}), Path("/data"))

        with self.assertRaises(RootReadOnlyError):
            resolver.write_dir("lora")

    def test_offline_root_raises(self):
        roots = [_root("lib", "/data/lib")]
        bindings = [_binding("lib", "lora", "loras", 0, is_write=True)]
        probe = FakeProbe({"lib": ("offline", "unplugged")})
        resolver = ModelRootResolver(FakeRepository(roots, bindings), probe, Path("/data"))

        with self.assertRaises(RootUnavailableError):
            resolver.write_dir("lora")

    def test_write_dir_ok(self):
        roots = [_root("home", "/data/depot")]
        bindings = [_binding("home", "lora", "loras", 0, is_write=True)]
        resolver = ModelRootResolver(FakeRepository(roots, bindings), FakeProbe({}), Path("/data/depot"))

        type_dir = resolver.write_dir("lora")

        self.assertEqual(type_dir.root_id, "home")
        self.assertEqual(type_dir.path, Path("/data/depot/loras"))


class TestTypeDirs(unittest.TestCase):

    def test_ordered_by_position(self):
        roots = [_root("home", "/data/depot"), _root("lib", "/data/lib")]
        bindings = [
            _binding("home", "lora", "loras", 1),
            _binding("lib", "lora", "loras", 0),
        ]
        resolver = ModelRootResolver(FakeRepository(roots, bindings), FakeProbe({}), Path("/data"))

        dirs = resolver.type_dirs("lora", online_only=False)

        self.assertEqual([d.root_id for d in dirs], ["lib", "home"])

    def test_online_only_filters_offline_roots(self):
        roots = [_root("home", "/data/depot"), _root("lib", "/data/lib")]
        bindings = [
            _binding("home", "lora", "loras", 0),
            _binding("lib", "lora", "loras", 1),
        ]
        probe = FakeProbe({"lib": ("offline", "unplugged")})
        resolver = ModelRootResolver(FakeRepository(roots, bindings), probe, Path("/data"))

        dirs = resolver.type_dirs("lora")

        self.assertEqual([d.root_id for d in dirs], ["home"])


class TestAssetDirAndContainment(unittest.TestCase):

    def _resolver(self):
        roots = [_root("home", "/data/depot", kind="home")]
        bindings = [_binding("home", "lora", "loras", 0)]
        return ModelRootResolver(FakeRepository(roots, bindings), FakeProbe({}), Path("/data"))

    def test_asset_dir_joins_home(self):
        resolver = self._resolver()

        self.assertEqual(resolver.asset_dir("taggers"), Path("/data/depot/taggers"))

    def test_asset_dir_rejects_traversal(self):
        resolver = self._resolver()

        with self.assertRaises(InvalidRelPathError):
            resolver.asset_dir("../evil")

    def test_physical_rejects_traversal(self):
        resolver = self._resolver()

        with self.assertRaises(InvalidRelPathError):
            resolver.physical(LogicalLocation("home", "lora", "../../etc/passwd"))

    def test_physical_rejects_absolute(self):
        resolver = self._resolver()

        with self.assertRaises(InvalidRelPathError):
            resolver.physical(LogicalLocation("home", "lora", "/etc/passwd"))

    def test_physical_ok(self):
        resolver = self._resolver()

        self.assertEqual(
            resolver.physical(LogicalLocation("home", "lora", "sdxl/x.safetensors")),
            Path("/data/depot/loras/sdxl/x.safetensors"),
        )


class _SlowPath:

    def __init__(self, delay):
        self._delay = delay

    def is_dir(self):
        time.sleep(self._delay)
        return True


class TestRootProbe(unittest.TestCase):

    def _root(self, path):
        return ModelRoot(
            id="r1", label="r1", path=path, kind="library",
            read_only=False, case_insensitive=False, state="online", state_reason=None,
        )

    def test_ttl_caches_between_calls(self):
        probe = RootProbe(ttl_seconds=10.0, timeout_seconds=1.0)
        calls = {"n": 0}

        class CountingPath:
            def is_dir(self_inner):
                calls["n"] += 1
                return True

        root = self._root(CountingPath())

        probe.state(root)
        probe.state(root)

        self.assertEqual(calls["n"], 1)

    def test_refresh_forces_reprobe(self):
        probe = RootProbe(ttl_seconds=10.0, timeout_seconds=1.0)
        calls = {"n": 0}

        class CountingPath:
            def is_dir(self_inner):
                calls["n"] += 1
                return True

        root = self._root(CountingPath())

        probe.state(root)
        probe.refresh(root.id)
        probe.state(root)

        self.assertEqual(calls["n"], 2)

    def test_timeout_reports_offline_without_blocking(self):
        probe = RootProbe(ttl_seconds=10.0, timeout_seconds=0.1)
        root = self._root(_SlowPath(2.0))

        started = time.monotonic()
        state, reason = probe.state(root)
        elapsed = time.monotonic() - started

        self.assertEqual(state, "offline")
        self.assertIn("timed out", reason)
        self.assertLess(elapsed, 1.0)


if __name__ == "__main__":
    unittest.main()
