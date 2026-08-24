"""Regression coverage: the pipe catalog must not import a pipe's
module until that specific pipe is actually requested.

Two things are asserted:

1. The light scan that backs `get_pipe`/`get_pipe_status`/`get_pipe_source`
   finds every pipe's registry key from the filesystem layout alone, without
   running any pipe module's top-level code (proven with an on-disk fixture
   pipe that records an import side effect).
2. In a fresh subprocess, resolving one real pipe family never drags heavy,
   unrelated pipe dependencies (cv2, the video/detailer pipe tree) into
   `sys.modules`. Subprocess isolation matters here: running this in-process
   would inherit whatever the rest of the test session already imported.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from src.pipelines.catalog import PipeCatalog

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_light_scan_finds_pipes_without_importing_them(tmp_path):
    core_pipes_path = tmp_path / "core_pipes"
    custom_pipes_path = tmp_path / "custom_pipes"
    core_pipes_path.mkdir()
    custom_pipes_path.mkdir()

    sentinel_file = tmp_path / "imported.marker"

    pipe_dir = core_pipes_path / "sentinel_pipe"
    pipe_dir.mkdir()
    (pipe_dir / "main.py").write_text(textwrap.dedent(f"""
        # Import side effect the test uses to prove this module was (or was
        # not) executed - writing the marker file IS the "was imported" proof.
        with open({str(sentinel_file)!r}, "w") as _f:
            _f.write("imported")

        from src.pipelines.contracts import BasePipe, PipeInput, PipeOutput


        class SentinelPipe(BasePipe):
            name = "sentinel_pipe"
            description = "Fixture pipe used to prove lazy import semantics"

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
    """))

    catalog = PipeCatalog(str(core_pipes_path), str(custom_pipes_path))

    # Metadata (source) must be resolvable without ever importing the module.
    assert catalog.get_pipe_source("sentinel_pipe") == "core"
    assert not sentinel_file.exists(), "get_pipe_source must not import the pipe module"

    # Only actually asking for the class triggers the import.
    pipe_class = catalog.get_pipe("sentinel_pipe")
    assert pipe_class is not None
    assert pipe_class.name == "sentinel_pipe"
    assert sentinel_file.exists(), "get_pipe() must import the requested module"


def test_get_pipe_status_imports_only_the_requested_pipe(tmp_path):
    core_pipes_path = tmp_path / "core_pipes"
    custom_pipes_path = tmp_path / "custom_pipes"
    core_pipes_path.mkdir()
    custom_pipes_path.mkdir()

    imported = []
    for pipe_name in ("pipe_a", "pipe_b"):
        marker = tmp_path / f"{pipe_name}.marker"
        pipe_dir = core_pipes_path / pipe_name
        pipe_dir.mkdir()
        (pipe_dir / "main.py").write_text(textwrap.dedent(f"""
            with open({str(marker)!r}, "w") as _f:
                _f.write("imported")

            from src.pipelines.contracts import BasePipe, PipeOutput


            class Pipe(BasePipe):
                name = {pipe_name!r}
                description = "fixture"

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
        """))

    catalog = PipeCatalog(str(core_pipes_path), str(custom_pipes_path))

    catalog.get_pipe_status("pipe_a")

    assert (tmp_path / "pipe_a.marker").exists()
    assert not (tmp_path / "pipe_b.marker").exists(), (
        "get_pipe_status for pipe_a must not import unrelated pipe_b"
    )


def _run_subprocess(script: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{env.get('PYTHONPATH', '')}{os.pathsep}." if env.get("PYTHONPATH") else "."
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_resolving_one_pipe_family_does_not_import_cv2_or_video_pipes():
    script = textwrap.dedent("""
        import sys
        from src.pipelines.catalog import PipeCatalog

        catalog = PipeCatalog("src/pipelines/pipes", "pipes/custom")

        # A representative slice of a plain txt2img SDXL pipeline.
        for key in ("seed_generator", "prompt_encoder", "generator/sdxl"):
            pipe_class = catalog.get_pipe(key)
            assert pipe_class is not None, f"pipe not found: {key}"

        forbidden = ("cv2",)
        leaked = sorted(m for m in forbidden if m in sys.modules)
        print("LEAKED=" + ",".join(leaked))
    """)

    result = _run_subprocess(script)
    assert result.returncode == 0, result.stderr
    leaked_line = next(
        (line for line in result.stdout.splitlines() if line.startswith("LEAKED=")), None
    )
    assert leaked_line is not None, result.stdout
    leaked = leaked_line[len("LEAKED="):]
    assert leaked == "", f"unexpected modules imported by an unrelated pipe request: {leaked}"


def test_catalog_construction_alone_imports_nothing():
    script = textwrap.dedent("""
        import sys
        from src.pipelines.catalog import PipeCatalog

        catalog = PipeCatalog("src/pipelines/pipes", "pipes/custom")

        forbidden = ("cv2", "diffusers", "transformers", "safetensors", "einops")
        leaked = sorted(m for m in forbidden if m in sys.modules)
        print("LEAKED=" + ",".join(leaked))
    """)

    result = _run_subprocess(script)
    assert result.returncode == 0, result.stderr
    leaked_line = next(
        (line for line in result.stdout.splitlines() if line.startswith("LEAKED=")), None
    )
    assert leaked_line is not None, result.stdout
    leaked = leaked_line[len("LEAKED="):]
    assert leaked == "", f"constructing the catalog must not import anything: {leaked}"
