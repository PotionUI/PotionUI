"""The preset requirement checker contract.

A preset's `requirements:` list (see docs/presets.md "Requirements") is a
list of typed entries, e.g. `{type: binary, name: ffmpeg}`. A
`RequirementChecker` evaluates one `type:` against this instance and returns
a `RequirementResult` - never raises, never blocks on real subprocess/network
work longer than it has to (the evaluator wraps every checker in its own
timeout regardless). Re-exported by `src.plugin_api.presets` for plugin
checkers; the registration bookkeeping (`RequirementCheckerRegistry`) lives in
`src.platform.plugins.requirement_checkers` instead, because that module must
not import `src.features`.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional, Protocol, Type, runtime_checkable

from src.features.models.collaborators import ModelIndexCollaborators

# "unknown" means the checker could not evaluate this entry here - a timeout,
# a checker that isn't registered for this process, a remote-only backend
# with no local reading to take. It is never treated as "missing": an
# admin's requirements panel should read "can't tell from here", not fail
# the preset closed.
RequirementStatus = Literal["ok", "missing", "unknown"]


@dataclass(frozen=True)
class RequirementAction:
    """A follow-up the admin UI can offer for a "missing" result - e.g. a
    button that opens the downloader for a missing model, or the Backends
    admin page for a missing/misconfigured engine. `payload` is free-form,
    interpreted by whichever frontend surface renders this action."""

    kind: Literal["open_downloader", "open_backends", "open_url"]
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RequirementResult:
    """The outcome of checking one `requirements:` entry."""

    status: RequirementStatus
    detail: str
    hint: Optional[str] = None
    action: Optional[RequirementAction] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "hint": self.hint,
            "action": (
                {"kind": self.action.kind, "payload": self.action.payload}
                if self.action is not None else None
            ),
        }


@dataclass(frozen=True)
class RequirementBackendInfo:
    """The preset's resolved backend, as much as a checker needs. Never the
    full engine-specific config type (a ComfyUI checker wants a `base_url`, a
    native one doesn't) - `config` carries whatever the backend's own
    `BaseBackendConfig` subclass declares, duck-typed here so this module
    never has to import every engine's config class."""

    id: str
    engine: str
    driver: str
    config: Any = None


@dataclass(frozen=True)
class RequirementContext:
    """Everything a checker can read about this instance, gathered once per
    evaluation by `src.features.presets.requirements.context_builder`. Never
    a service locator: checkers take this by value and never reach into a
    container themselves, so a plugin checker only ever needs to import
    `src.plugin_api.presets`."""

    # The models feature's collaborators bundle (catalog, tag_repo, model_repo,
    # ...) - the same object `PromptImporter`/LLM tools are handed. `None`
    # only in a test/tooling context that never wired one up.
    models: Optional[ModelIndexCollaborators]
    gpu_available: bool
    # `None` when there is no local reading to take (no GPU, or the preset's
    # resolved backend is a remote worker) - a `vram_min_gb` checker must
    # resolve to "unknown" rather than guess.
    gpu_total_vram_gb: Optional[float]
    backend: Optional[RequirementBackendInfo]
    # `sys.platform` ("linux", "darwin", "win32", ...).
    platform: str


@runtime_checkable
class RequirementChecker(Protocol):
    """One requirement `type:`'s evaluator. Pure Python - no subprocess, no
    shell, no network I/O beyond what a plugin's own checker chooses to do
    (and accepts the evaluator's timeout for)."""

    # The `requirements:` entry `type:` name this checker evaluates.
    type: str
    # A pydantic `BaseModel` describing this type's own entry arguments
    # (`hint`/`optional` plus whatever `type`-specific fields it takes) - used
    # by `PresetLinter` to validate `preset.yml` entries, and by `check()`
    # itself to parse `spec`.
    schema: Type[Any]

    async def check(self, spec: Dict[str, Any], ctx: RequirementContext) -> RequirementResult:
        """Evaluate one `requirements:` entry (`spec`, the raw dict as
        authored in `preset.yml`, including its `type:` key) against `ctx`.
        Should not raise for an expected failure (the thing genuinely isn't
        there) - return a "missing"/"unknown" `RequirementResult` instead. An
        actually-raised exception is still handled by the evaluator (resolves
        to "unknown"), but loses the caller's chance to say why in `detail`.
        """
        ...


__all__ = [
    "RequirementStatus",
    "RequirementAction",
    "RequirementResult",
    "RequirementBackendInfo",
    "RequirementContext",
    "RequirementChecker",
]
