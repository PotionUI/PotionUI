"""Core requirement checkers, environment mocked - no real subprocess/filesystem/GPU calls."""

import importlib.metadata
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from src.features.presets.requirements.builtin import (
    BinaryRequirementChecker,
    ModelRequirementChecker,
    PlatformRequirementChecker,
    PythonPackageRequirementChecker,
    VramMinGbRequirementChecker,
)
from src.features.presets.requirements.contracts import RequirementContext


def _ctx(**overrides) -> RequirementContext:
    defaults = dict(
        models=None,
        gpu_available=False,
        gpu_total_vram_gb=None,
        backend=None,
        platform="linux",
    )
    defaults.update(overrides)
    return RequirementContext(**defaults)


# ---------------------------------------------------------------------------
# binary
# ---------------------------------------------------------------------------

class TestBinaryRequirementChecker:
    @pytest.mark.asyncio
    async def test_found_on_path(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}" if name == "ffmpeg" else None)
        checker = BinaryRequirementChecker()

        result = await checker.check({"type": "binary", "name": "ffmpeg"}, _ctx())

        assert result.status == "ok"
        assert "ffmpeg" in result.detail

    @pytest.mark.asyncio
    async def test_missing_carries_hint(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        checker = BinaryRequirementChecker()

        result = await checker.check(
            {"type": "binary", "name": "ffmpeg", "hint": "install ffmpeg"}, _ctx()
        )

        assert result.status == "missing"
        assert result.hint == "install ffmpeg"

    @pytest.mark.asyncio
    async def test_names_first_match_wins(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg.exe" if name == "ffmpeg.exe" else None)
        checker = BinaryRequirementChecker()

        result = await checker.check({"type": "binary", "names": ["ffmpeg", "ffmpeg.exe"]}, _ctx())

        assert result.status == "ok"

    def test_schema_requires_name_or_names(self):
        with pytest.raises(ValidationError):
            BinaryRequirementChecker.schema.model_validate({"type": "binary"})

    def test_describe_prefers_name(self):
        checker = BinaryRequirementChecker()
        assert checker.describe({"type": "binary", "name": "ffmpeg"}) == "ffmpeg"

    def test_describe_falls_back_to_first_of_names(self):
        checker = BinaryRequirementChecker()
        assert checker.describe({"type": "binary", "names": ["ffmpeg", "ffmpeg.exe"]}) == "ffmpeg"


# ---------------------------------------------------------------------------
# python_package
# ---------------------------------------------------------------------------

class TestPythonPackageRequirementChecker:
    @pytest.mark.asyncio
    async def test_installed_no_version_constraint(self, monkeypatch):
        monkeypatch.setattr(importlib.metadata, "version", lambda name: "1.2.3")
        checker = PythonPackageRequirementChecker()

        result = await checker.check({"type": "python_package", "name": "xformers"}, _ctx())

        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_not_installed(self, monkeypatch):
        def _raise(name):
            raise importlib.metadata.PackageNotFoundError(name)
        monkeypatch.setattr(importlib.metadata, "version", _raise)
        checker = PythonPackageRequirementChecker()

        result = await checker.check({"type": "python_package", "name": "xformers"}, _ctx())

        assert result.status == "missing"

    @pytest.mark.asyncio
    async def test_version_constraint_satisfied(self, monkeypatch):
        monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.0.29")
        checker = PythonPackageRequirementChecker()

        result = await checker.check(
            {"type": "python_package", "name": "xformers", "version": ">=0.0.28"}, _ctx()
        )

        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_version_constraint_not_satisfied(self, monkeypatch):
        monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.0.20")
        checker = PythonPackageRequirementChecker()

        result = await checker.check(
            {"type": "python_package", "name": "xformers", "version": ">=0.0.28"}, _ctx()
        )

        assert result.status == "missing"

    @pytest.mark.asyncio
    async def test_unparseable_constraint_is_unknown(self, monkeypatch):
        monkeypatch.setattr(importlib.metadata, "version", lambda name: "1.0.0")
        checker = PythonPackageRequirementChecker()

        result = await checker.check(
            {"type": "python_package", "name": "xformers", "version": "not-a-specifier!!"}, _ctx()
        )

        assert result.status == "unknown"

    def test_describe_with_version(self):
        checker = PythonPackageRequirementChecker()
        assert checker.describe({"type": "python_package", "name": "xformers", "version": ">=0.0.28"}) == "xformers>=0.0.28"

    def test_describe_without_version(self):
        checker = PythonPackageRequirementChecker()
        assert checker.describe({"type": "python_package", "name": "xformers"}) == "xformers"


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------

class TestModelRequirementChecker:
    @pytest.mark.asyncio
    async def test_hash_match_present_and_available(self):
        model = SimpleNamespace(filename="checkpoint.safetensors", is_available=True)
        models = SimpleNamespace(model_repo=MagicMock(get_by_sha256=MagicMock(return_value=model)))
        checker = ModelRequirementChecker()

        result = await checker.check({"type": "model", "hash": "abc123"}, _ctx(models=models))

        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_hash_match_but_unavailable_is_missing(self):
        model = SimpleNamespace(filename="checkpoint.safetensors", is_available=False)
        models = SimpleNamespace(model_repo=MagicMock(get_by_sha256=MagicMock(return_value=model)))
        checker = ModelRequirementChecker()

        result = await checker.check({"type": "model", "hash": "abc123"}, _ctx(models=models))

        assert result.status == "missing"
        assert result.action is not None
        assert result.action.kind == "open_downloader"

    @pytest.mark.asyncio
    async def test_hash_not_found(self):
        models = SimpleNamespace(model_repo=MagicMock(get_by_sha256=MagicMock(return_value=None)))
        checker = ModelRequirementChecker()

        result = await checker.check({"type": "model", "hash": "abc123"}, _ctx(models=models))

        assert result.status == "missing"

    @pytest.mark.asyncio
    async def test_tag_not_found(self):
        models = SimpleNamespace(tag_repo=MagicMock(get_tag_by_name=MagicMock(return_value=None)))
        checker = ModelRequirementChecker()

        result = await checker.check({"type": "model", "tag": "flux-klein-9b"}, _ctx(models=models))

        assert result.status == "missing"

    @pytest.mark.asyncio
    async def test_tag_found_with_available_model(self):
        tag = SimpleNamespace(id="tag-1")
        matched_model = SimpleNamespace(is_available=True)
        models = SimpleNamespace(
            tag_repo=MagicMock(get_tag_by_name=MagicMock(return_value=tag)),
            model_repo=MagicMock(get_all=MagicMock(return_value=[matched_model])),
        )
        checker = ModelRequirementChecker()

        result = await checker.check({"type": "model", "tag": "flux-klein-9b"}, _ctx(models=models))

        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_no_models_catalog_is_unknown(self):
        checker = ModelRequirementChecker()

        result = await checker.check({"type": "model", "hash": "abc123"}, _ctx(models=None))

        assert result.status == "unknown"

    def test_schema_rejects_both_tag_and_hash(self):
        with pytest.raises(ValidationError):
            ModelRequirementChecker.schema.model_validate({"type": "model", "tag": "a", "hash": "b"})

    def test_schema_rejects_neither_tag_nor_hash(self):
        with pytest.raises(ValidationError):
            ModelRequirementChecker.schema.model_validate({"type": "model"})

    def test_describe_prefers_tag(self):
        checker = ModelRequirementChecker()
        assert checker.describe({"type": "model", "tag": "flux-klein-9b"}) == "flux-klein-9b"

    def test_describe_falls_back_to_short_hash(self):
        checker = ModelRequirementChecker()
        assert checker.describe({"type": "model", "hash": "abcdef1234567890"}) == "abcdef123456"


# ---------------------------------------------------------------------------
# vram_min_gb
# ---------------------------------------------------------------------------

class TestVramMinGbRequirementChecker:
    @pytest.mark.asyncio
    async def test_enough_vram(self):
        checker = VramMinGbRequirementChecker()

        result = await checker.check({"type": "vram_min_gb", "gb": 16}, _ctx(gpu_total_vram_gb=24.0))

        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_not_enough_vram(self):
        checker = VramMinGbRequirementChecker()

        result = await checker.check({"type": "vram_min_gb", "gb": 16}, _ctx(gpu_total_vram_gb=8.0))

        assert result.status == "missing"

    @pytest.mark.asyncio
    async def test_no_reading_is_unknown(self):
        checker = VramMinGbRequirementChecker()

        result = await checker.check({"type": "vram_min_gb", "gb": 16}, _ctx(gpu_total_vram_gb=None))

        assert result.status == "unknown"

    def test_describe(self):
        checker = VramMinGbRequirementChecker()
        assert checker.describe({"type": "vram_min_gb", "gb": 16}) == "16 GB"


# ---------------------------------------------------------------------------
# platform
# ---------------------------------------------------------------------------

class TestPlatformRequirementChecker:
    @pytest.mark.asyncio
    async def test_matching_platform(self):
        checker = PlatformRequirementChecker()

        result = await checker.check({"type": "platform", "os": ["linux", "darwin"]}, _ctx(platform="linux"))

        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_non_matching_platform(self):
        checker = PlatformRequirementChecker()

        result = await checker.check({"type": "platform", "os": ["darwin"]}, _ctx(platform="win32"))

        assert result.status == "missing"

    @pytest.mark.asyncio
    async def test_windows_maps_to_win32_prefix(self):
        checker = PlatformRequirementChecker()

        result = await checker.check({"type": "platform", "os": ["windows"]}, _ctx(platform="win32"))

        assert result.status == "ok"

    def test_describe_joins_os_list(self):
        checker = PlatformRequirementChecker()
        assert checker.describe({"type": "platform", "os": ["linux", "darwin"]}) == "linux, darwin"
