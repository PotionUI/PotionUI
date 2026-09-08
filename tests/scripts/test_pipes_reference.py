"""Tests for scripts/pipes_reference.py's markdown rendering and its
import-failure capture, against a tiny fake catalog rather than the real
(slow, environment-dependent) pipe tree.
"""

import logging

import scripts.pipes_reference as pr
from scripts.pipes_reference import (
    _ImportFailureCollector,
    _registry_name_from_module,
    render_pipes_md_from_catalog,
)
from src.pipelines.contracts import BasePipe, IOType, PipeConfigSpec, PipeInputSpec, PipeOutputSpec, PipeStatus


class _FakeCatalog:
    """Duck-types the subset of `PipeCatalog` `PipesDocumenter` reads."""

    def __init__(self, pipes):
        self.pipes = pipes

    def get_pipe_status(self, name):
        return PipeStatus.INSTALLED


class _AlphaPipe(BasePipe):
    name = "aaa_family"
    description = "Alpha family pipe."

    def process(self, pipe_input, generation_outputs):  # pragma: no cover - never called
        raise NotImplementedError

    @classmethod
    def get_default_config(cls):
        return {"threshold": 0.5}

    @classmethod
    def inputs(cls):
        return [PipeInputSpec("image", IOType.IMAGE, required=True, description="Source image")]

    @classmethod
    def outputs(cls):
        return [PipeOutputSpec("image", IOType.IMAGE, description="Result image", is_array=True)]

    @classmethod
    def configuration(cls):
        return [PipeConfigSpec("threshold", float, 0.5, description="Cut-off", min_value=0.0, max_value=1.0)]


class _ZetaVariantPipe(BasePipe):
    name = "zzz_family/variant"
    description = "Zeta family variant pipe."

    def process(self, pipe_input, generation_outputs):  # pragma: no cover - never called
        raise NotImplementedError

    @classmethod
    def get_default_config(cls):
        return {}

    @classmethod
    def inputs(cls):
        return []

    @classmethod
    def outputs(cls):
        return []

    @classmethod
    def configuration(cls):
        return []


def _fake_catalog():
    return _FakeCatalog({
        "zzz_family/variant": _ZetaVariantPipe,
        "aaa_family": _AlphaPipe,
    })


class TestRenderPipesMdFromCatalog:
    def test_deterministic_across_runs(self):
        catalog = _fake_catalog()
        first = render_pipes_md_from_catalog(catalog, {})
        second = render_pipes_md_from_catalog(catalog, {})
        assert first == second

    def test_families_and_pipes_are_sorted(self):
        md = render_pipes_md_from_catalog(_fake_catalog(), {})
        # `aaa_family` must render before `zzz_family` regardless of the
        # catalog's own (here deliberately reversed) dict insertion order.
        assert md.index("## aaa_family") < md.index("## zzz_family")

    def test_pipe_description_inputs_outputs_configuration_render(self):
        md = render_pipes_md_from_catalog(_fake_catalog(), {})
        assert "`aaa_family`" in md
        assert "Alpha family pipe." in md
        assert "| `image` | `IMAGE` | yes | no | Source image |" in md
        assert "| `image` | `IMAGE` | yes | Result image |" in md
        assert "| `threshold` | `float` | `0.5` | no | — | 0.0 | 1.0 | Cut-off |" in md

    def test_pipe_with_no_inputs_outputs_configuration_says_so(self):
        md = render_pipes_md_from_catalog(_fake_catalog(), {})
        assert "_No inputs._" in md
        assert "**Outputs**: none." in md
        assert "**Configuration**: none." in md

    def test_import_failure_gets_a_subsection_not_dropped(self):
        md = render_pipes_md_from_catalog(
            _fake_catalog(), {"broken_family/thing": "ModuleNotFoundError: no foo"}
        )
        assert "## broken_family" in md
        assert "`broken_family/thing`" in md
        assert "**Failed to import:** `ModuleNotFoundError: no foo`" in md

    def test_import_failure_pipe_gets_no_io_tables(self):
        md = render_pipes_md_from_catalog(
            _fake_catalog(), {"broken_family/thing": "boom"}
        )
        section = md.split('`broken_family/thing`', 1)[1].split("\n## ", 1)[0]
        assert "**Inputs**" not in section
        assert "**Configuration**" not in section


class TestRegistryNameFromModule:
    def test_main_pipe(self):
        assert _registry_name_from_module("pipes.mask_preprocessor") == "mask_preprocessor"

    def test_variant_pipe(self):
        assert _registry_name_from_module("pipes.checkpoint_loader.sdxl") == "checkpoint_loader/sdxl"


class TestImportFailureCollector:
    def test_captures_and_maps_module_name_to_registry_name(self):
        collector = _ImportFailureCollector()
        logger = logging.getLogger("scripts.pipes_reference.test_logger")
        logger.addHandler(collector)
        try:
            logger.error("Error loading pipe pipes.checkpoint_loader.sdxl: boom")
        finally:
            logger.removeHandler(collector)
        assert collector.failures == {"checkpoint_loader/sdxl": "boom"}

    def test_ignores_unrelated_error_records(self):
        collector = _ImportFailureCollector()
        logger = logging.getLogger("scripts.pipes_reference.test_logger2")
        logger.addHandler(collector)
        try:
            logger.error("Something unrelated went wrong")
        finally:
            logger.removeHandler(collector)
        assert collector.failures == {}


def _patch_paths(monkeypatch, tmp_path):
    pipes_md = tmp_path / "pipes.md"
    context_md = tmp_path / "context.md"
    monkeypatch.setattr(pr, "PIPES_MD_PATH", pipes_md)
    monkeypatch.setattr(pr, "CONTEXT_MD_PATH", context_md)
    monkeypatch.setattr(pr, "render_context_md", lambda repo_root: "CONTEXT\n")
    return pipes_md, context_md


class TestCheckEnvironmentTolerance:
    """`check()` must never depend on the checking box's ability to import
    every pipe: a clean environment verifies the whole file, an
    import-limited one verifies only what it can and warns about the rest -
    it must never silently pass real drift in the pipes it CAN render."""

    def test_clean_environment_full_compare_passes(self, tmp_path, monkeypatch):
        pipes_md, context_md = _patch_paths(monkeypatch, tmp_path)
        catalog = _fake_catalog()
        monkeypatch.setattr(pr, "discover_pipes", lambda repo_root: (catalog, {}))
        pipes_md.write_text(pr.render_pipes_md_from_catalog(catalog, {}), encoding="utf-8")
        context_md.write_text("CONTEXT\n", encoding="utf-8")

        report = pr.check(tmp_path)

        assert report.errors == []
        assert report.warnings == []
        assert report.ok is True

    def test_clean_environment_detects_real_drift(self, tmp_path, monkeypatch):
        pipes_md, context_md = _patch_paths(monkeypatch, tmp_path)
        catalog = _fake_catalog()
        monkeypatch.setattr(pr, "discover_pipes", lambda repo_root: (catalog, {}))
        pipes_md.write_text("stale content\n", encoding="utf-8")
        context_md.write_text("CONTEXT\n", encoding="utf-8")

        report = pr.check(tmp_path)

        assert report.ok is False
        assert any("pipes.md" in e and "out of date" in e for e in report.errors)

    def test_import_limited_environment_warns_and_skips_failed_pipes(self, tmp_path, monkeypatch):
        pipes_md, context_md = _patch_paths(monkeypatch, tmp_path)
        catalog = _fake_catalog()
        import_errors = {"broken_family/thing": "boom"}
        monkeypatch.setattr(pr, "discover_pipes", lambda repo_root: (catalog, import_errors))
        # A "committed" file as a clean environment would have written it -
        # correct blocks for the importable pipes, nothing about the pipe
        # this environment can't import (its actual content is unknowable
        # here, so the check must not require any particular content for it).
        documenter_content = pr.render_pipes_md_from_catalog(catalog, {})
        pipes_md.write_text(documenter_content, encoding="utf-8")
        context_md.write_text("CONTEXT\n", encoding="utf-8")

        report = pr.check(tmp_path)

        assert report.errors == []
        assert len(report.warnings) == 1
        assert "drift check skipped for 1 pipe" in report.warnings[0]
        assert "broken_family/thing" in report.warnings[0]

    def test_import_limited_environment_still_catches_drift_in_importable_pipes(self, tmp_path, monkeypatch):
        pipes_md, context_md = _patch_paths(monkeypatch, tmp_path)
        catalog = _fake_catalog()
        import_errors = {"broken_family/thing": "boom"}
        monkeypatch.setattr(pr, "discover_pipes", lambda repo_root: (catalog, import_errors))
        pipes_md.write_text("nothing here matches any real pipe block\n", encoding="utf-8")
        context_md.write_text("CONTEXT\n", encoding="utf-8")

        report = pr.check(tmp_path)

        # Real drift in a pipe this environment CAN render is still an error -
        # the tolerance only ever covers pipes it can't render.
        assert report.ok is False
        assert any("aaa_family" in e for e in report.errors)
        assert any("zzz_family/variant" in e for e in report.errors)
        assert len(report.warnings) == 1


class TestMainWriteGating:
    """Default `main()` must never commit a partial docs/pipes.md: any import
    failure writes nothing and exits non-zero unless explicitly overridden."""

    def test_import_errors_without_flag_write_nothing_and_exit_nonzero(self, tmp_path, monkeypatch):
        pipes_md, context_md = _patch_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            pr, "discover_pipes", lambda repo_root: (_fake_catalog(), {"broken_family/thing": "boom"})
        )

        exit_code = pr.main([])

        assert exit_code == 1
        assert not pipes_md.exists()
        assert not context_md.exists()

    def test_allow_import_errors_writes_with_error_sections(self, tmp_path, monkeypatch):
        pipes_md, context_md = _patch_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(
            pr, "discover_pipes", lambda repo_root: (_fake_catalog(), {"broken_family/thing": "boom"})
        )

        exit_code = pr.main(["--allow-import-errors"])

        assert exit_code == 0
        content = pipes_md.read_text(encoding="utf-8")
        assert "**Failed to import:** `boom`" in content
        assert context_md.exists()

    def test_clean_environment_writes_normally_without_flag(self, tmp_path, monkeypatch):
        pipes_md, context_md = _patch_paths(monkeypatch, tmp_path)
        monkeypatch.setattr(pr, "discover_pipes", lambda repo_root: (_fake_catalog(), {}))

        exit_code = pr.main([])

        assert exit_code == 0
        assert pipes_md.exists()
        assert context_md.exists()
        assert "Failed to import" not in pipes_md.read_text(encoding="utf-8")
