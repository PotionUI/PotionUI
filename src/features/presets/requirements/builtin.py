"""Core preset requirement checkers: `binary`, `python_package`, `model`,
`vram_min_gb`, `platform`. Engine-specific checkers (a ComfyUI custom node or
model) are NOT here - they belong to the plugin that owns that engine,
registered via `requirement_checkers:` in its `manifest.yml` (see
`src.platform.plugins.requirement_checkers`).

Every checker is pure Python: no subprocess, no shell, no network I/O.
"""

import importlib.metadata
import shutil
from typing import Any, Dict, List, Optional

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.features.presets.requirements.contracts import (
    RequirementAction,
    RequirementContext,
    RequirementResult,
)
from src.platform.plugins.requirement_checkers import (
    RequirementCheckerRegistration,
    RequirementCheckerRegistry,
)


class _RequirementEntryModel(BaseModel):
    """Fields every core checker's schema shares - `type:` (the entry's own
    discriminator, needed here too since `extra="forbid"` subclasses reject
    any key they don't declare) plus the two universal knobs every entry may
    carry regardless of type. Not exported: a checker's own schema always
    subclasses this rather than composing it, so `model_validate(spec)` sees
    one flat model."""

    model_config = ConfigDict(extra="forbid")

    type: str
    hint: Optional[str] = None
    optional: bool = False


# ---------------------------------------------------------------------------
# binary
# ---------------------------------------------------------------------------


class BinaryRequirementSchema(_RequirementEntryModel):
    """`{type: binary, name: ffmpeg}` or `{type: binary, names: [ffmpeg, ffmpeg.exe]}`
    - `names:` lets a preset list per-platform alternatives; the first one
    found on PATH wins."""

    name: Optional[str] = None
    names: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_one_name(self) -> "BinaryRequirementSchema":
        if not self.name and not self.names:
            raise ValueError("binary requirement needs 'name' or 'names'")
        return self

    @property
    def candidates(self) -> List[str]:
        return self.names or [self.name]


class BinaryRequirementChecker:
    type = "binary"
    schema = BinaryRequirementSchema

    async def check(self, spec: Dict[str, Any], ctx: RequirementContext) -> RequirementResult:
        parsed = self.schema.model_validate(spec)
        for candidate in parsed.candidates:
            found = shutil.which(candidate)
            if found:
                return RequirementResult(status="ok", detail=f"'{candidate}' found on PATH ({found})")
        joined = "', '".join(parsed.candidates)
        return RequirementResult(
            status="missing",
            detail=f"none of '{joined}' found on PATH",
            hint=parsed.hint,
        )

    def describe(self, spec: Dict[str, Any]) -> str:
        name = spec.get("name")
        if name:
            return name
        names = spec.get("names") or []
        return names[0] if names else "binary"


# ---------------------------------------------------------------------------
# python_package
# ---------------------------------------------------------------------------


class PythonPackageRequirementSchema(_RequirementEntryModel):
    """`{type: python_package, name: xformers, version: ">=0.0.28"}` -
    `version:` is a PEP 440 specifier set; omit it to only require the
    package be importable at any version."""

    name: str
    version: Optional[str] = None


class PythonPackageRequirementChecker:
    type = "python_package"
    schema = PythonPackageRequirementSchema

    async def check(self, spec: Dict[str, Any], ctx: RequirementContext) -> RequirementResult:
        parsed = self.schema.model_validate(spec)
        try:
            installed = importlib.metadata.version(parsed.name)
        except importlib.metadata.PackageNotFoundError:
            return RequirementResult(
                status="missing",
                detail=f"'{parsed.name}' is not installed",
                hint=parsed.hint,
            )

        if parsed.version:
            try:
                satisfied = SpecifierSet(parsed.version).contains(Version(installed), prereleases=True)
            except (InvalidSpecifier, InvalidVersion):
                return RequirementResult(
                    status="unknown",
                    detail=(
                        f"could not evaluate version constraint '{parsed.version}' "
                        f"against installed '{parsed.name}' {installed}"
                    ),
                )
            if not satisfied:
                return RequirementResult(
                    status="missing",
                    detail=f"'{parsed.name}' {installed} installed, requires {parsed.version}",
                    hint=parsed.hint,
                )

        return RequirementResult(status="ok", detail=f"'{parsed.name}' {installed} installed")

    def describe(self, spec: Dict[str, Any]) -> str:
        name = spec.get("name") or "python_package"
        version = spec.get("version")
        return f"{name}{version}" if version else name


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------


class ModelRequirementSchema(_RequirementEntryModel):
    """`{type: model, tag: "flux-klein-9b"}` or `{type: model, hash: "<sha256>"}`
    - exactly one of `tag`/`hash`.

    `tag` matches an admin `Tag` **name** - the same tagging concept a preset's
    `configuration: {type: model_tags}` already filters checkpoint/LoRA
    pickers by (docs/presets.md "Configuration (admin-set)"): an admin tags
    the checkpoint they installed with a tag named e.g. "flux-klein-9b", and
    this requirement is satisfied once some model in the depot carries it.
    There is no separate marketplace-identifier registry in core (a
    CivitAI/HF id is a provider concern, not a depot one) - a plugin wanting
    that lookup registers its own `model`-family checker under a different
    `type:` name.
    """

    tag: Optional[str] = None
    hash: Optional[str] = None

    @model_validator(mode="after")
    def _require_exactly_one(self) -> "ModelRequirementSchema":
        if bool(self.tag) == bool(self.hash):
            raise ValueError("model requirement needs exactly one of 'tag' or 'hash'")
        return self


class ModelRequirementChecker:
    type = "model"
    schema = ModelRequirementSchema

    async def check(self, spec: Dict[str, Any], ctx: RequirementContext) -> RequirementResult:
        parsed = self.schema.model_validate(spec)

        if ctx.models is None:
            return RequirementResult(status="unknown", detail="no model catalog available to check against")

        if parsed.hash:
            model = ctx.models.model_repo.get_by_sha256(parsed.hash, include_providers=False)
            if model is not None and model.is_available:
                return RequirementResult(status="ok", detail=f"model '{model.filename}' present (hash match)")
            return RequirementResult(
                status="missing",
                detail=f"no available model with hash '{parsed.hash}' found in the depot",
                hint=parsed.hint,
                action=RequirementAction(kind="open_downloader", payload={"hash": parsed.hash}),
            )

        tag = ctx.models.tag_repo.get_tag_by_name(parsed.tag)
        if tag is None:
            return RequirementResult(
                status="missing",
                detail=f"no tag named '{parsed.tag}' exists - tag the model you installed for it with this name",
                hint=parsed.hint,
                action=RequirementAction(kind="open_downloader", payload={"tag": parsed.tag}),
            )

        matches = ctx.models.model_repo.get_all(
            any_tag_ids=[tag.id], limit=1, include_providers=False, include_tags=False,
        )
        if any(m.is_available for m in matches):
            return RequirementResult(status="ok", detail=f"a model tagged '{parsed.tag}' is present")
        return RequirementResult(
            status="missing",
            detail=f"no available model tagged '{parsed.tag}' found in the depot",
            hint=parsed.hint,
            action=RequirementAction(kind="open_downloader", payload={"tag": parsed.tag}),
        )

    def describe(self, spec: Dict[str, Any]) -> str:
        tag = spec.get("tag")
        if tag:
            return tag
        model_hash = spec.get("hash")
        if model_hash:
            return model_hash[:12]
        return "model"


# ---------------------------------------------------------------------------
# vram_min_gb
# ---------------------------------------------------------------------------


class VramMinGbRequirementSchema(_RequirementEntryModel):
    """`{type: vram_min_gb, gb: 16}`."""

    gb: float

    @model_validator(mode="after")
    def _positive(self) -> "VramMinGbRequirementSchema":
        if self.gb <= 0:
            raise ValueError("vram_min_gb.gb must be a positive number")
        return self


class VramMinGbRequirementChecker:
    type = "vram_min_gb"
    schema = VramMinGbRequirementSchema
    # The physical VRAM total is a property of the backend that would
    # actually run the preset, not of this host in the abstract - a preset
    # with several candidate backends of the same engine (e.g. a local
    # native backend and a native_remote worker) can have one reading per
    # candidate, so this must be re-evaluated per backend rather than once
    # and shared (see contracts.RequirementChecker's scope attribute).
    scope = "backend"

    async def check(self, spec: Dict[str, Any], ctx: RequirementContext) -> RequirementResult:
        parsed = self.schema.model_validate(spec)
        if ctx.gpu_total_vram_gb is None:
            detail = ctx.gpu_unavailable_reason or (
                "no local VRAM reading available (no GPU on this host, or the preset's backend is remote)"
            )
            return RequirementResult(status="unknown", detail=detail)
        if ctx.gpu_total_vram_gb + 1e-6 >= parsed.gb:
            return RequirementResult(
                status="ok",
                detail=f"{ctx.gpu_total_vram_gb:.1f} GB VRAM available, requires {parsed.gb:g} GB",
            )
        return RequirementResult(
            status="missing",
            detail=f"{ctx.gpu_total_vram_gb:.1f} GB VRAM available, requires {parsed.gb:g} GB",
            hint=parsed.hint,
        )

    def describe(self, spec: Dict[str, Any]) -> str:
        gb = spec.get("gb")
        try:
            return f"{float(gb):g} GB"
        except (TypeError, ValueError):
            return "vram_min_gb"


# ---------------------------------------------------------------------------
# platform
# ---------------------------------------------------------------------------

_PLATFORM_PREFIXES = {"linux": "linux", "darwin": "darwin", "windows": "win32"}


class PlatformRequirementSchema(_RequirementEntryModel):
    """`{type: platform, os: [linux, darwin]}` - `os:` values are `linux`,
    `darwin`, or `windows`."""

    os: List[str]

    @model_validator(mode="after")
    def _validate_os(self) -> "PlatformRequirementSchema":
        if not self.os:
            raise ValueError("platform requirement needs a non-empty 'os' list")
        unknown = sorted(set(self.os) - set(_PLATFORM_PREFIXES))
        if unknown:
            raise ValueError(f"platform requirement has unknown os value(s) {unknown}, expected {sorted(_PLATFORM_PREFIXES)}")
        return self


class PlatformRequirementChecker:
    type = "platform"
    schema = PlatformRequirementSchema

    async def check(self, spec: Dict[str, Any], ctx: RequirementContext) -> RequirementResult:
        parsed = self.schema.model_validate(spec)
        allowed_prefixes = tuple(_PLATFORM_PREFIXES[o] for o in parsed.os)
        if ctx.platform.startswith(allowed_prefixes):
            return RequirementResult(status="ok", detail=f"running on '{ctx.platform}'")
        return RequirementResult(
            status="missing",
            detail=f"running on '{ctx.platform}', preset requires one of {parsed.os}",
            hint=parsed.hint,
        )

    def describe(self, spec: Dict[str, Any]) -> str:
        os_list = spec.get("os") or []
        return ", ".join(os_list) if os_list else "platform"


def register_builtin_requirement_checkers(registry: RequirementCheckerRegistry) -> None:
    """Register the five core checkers onto `registry`. Called once at
    startup (`src.bootstrap.container`) against the shared
    `requirement_checker_registry` singleton, and lazily by `PresetLinter`
    for standalone (no-container) tools like `scripts/preset_lint.py` -
    mirrors `register_builtin_fields`."""
    for checker in (
        BinaryRequirementChecker(),
        PythonPackageRequirementChecker(),
        ModelRequirementChecker(),
        VramMinGbRequirementChecker(),
        PlatformRequirementChecker(),
    ):
        registry.register(RequirementCheckerRegistration(type_name=checker.type, checker=checker, source="core"))
