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

from src.features.backends.base_backend import ExecutionDeviceEvidence
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
    # The backend's admin-set display name, e.g. "My ComfyUI Server" - not
    # read by any core checker, only surfaced to the `requirements` API/UI
    # so a per-backend result can be labeled without a second lookup.
    name: str = ""
    # The resolved backend INSTANCE's own `resolve_execution_device()`
    # evidence (see `src.features.backends.base_backend.
    # ExecutionDeviceEvidence`) - `kind` one of "this_host_gpu" (with a
    # `gpu_index`), "no_gpu", "remote", or "unestablished" (the default,
    # e.g. no backend was resolved at all). This is the ONLY thing
    # `vram_min_gb` trusts to decide whether this process's own GPU reading
    # applies to a given backend, and only when the reading's own device
    # index matches `gpu_index` - never the driver name (a plugin driver
    # containing "remote" in its name proves nothing, and one that doesn't
    # is not evidence of locality either), and never just "some GPU is
    # present" (a native backend configured for `cuda:1` is not satisfied
    # by a monitor bound to GPU 0).
    execution_device: ExecutionDeviceEvidence = ExecutionDeviceEvidence(kind="unestablished")


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
    # A short, human-readable note on why `gpu_total_vram_gb` ended up
    # `None` for a backend that otherwise looked like it should have a
    # local reading - identity unestablished, an identity mismatch, a
    # preset-authored per-pipe device override, or an explicit "no GPU
    # configured" - set by `context_builder.build_requirement_context_for_backend`.
    # `None` when there's nothing more specific to say than the checker's
    # own generic detail (no backend, remote, unestablished, or a reading
    # WAS taken).
    gpu_unavailable_reason: Optional[str] = None


@runtime_checkable
class RequirementChecker(Protocol):
    """One requirement `type:`'s evaluator. Pure Python - no subprocess, no
    shell, no network I/O beyond what a plugin's own checker chooses to do
    (and accepts the evaluator's timeout for).

    A checker may additionally implement `describe(spec) -> str` (not a
    formal Protocol member - purely duck-typed, so an existing checker that
    doesn't define it keeps working unchanged): a short, human name for one
    entry, used by `GET /api/presets/{preset_id}/requirements` to label a
    result (e.g. a filename, a tag, "16 GB"). Without it, the endpoint falls
    back to the entry's first string-valued field besides `type`/`hint`/
    `optional`, else the type name itself.

    A checker may also declare a `timeout_s: float` class/instance attribute
    (same duck-typing - absent means the evaluator's own default) to override
    how long `evaluate_preset_requirements` waits for its `check()` before
    resolving the entry to "unknown". The default (5s,
    `evaluator.CHECK_TIMEOUT_SECONDS`) suits a local check; a checker whose
    `check()` does a real network round trip against a remote server (e.g. a
    ComfyUI custom-node/model check fetching a big `/object_info`) should set
    a longer one rather than let a slow-but-live server always read as
    "unknown".

    A checker may also declare a `scope: Literal["host", "backend"] = "host"`
    class/instance attribute (same duck-typing - absent means "host"):
    "host" (every core checker) means the entry answers something true of
    this process regardless of which backend of the preset's engine ends up
    executing it (a binary on PATH, this host's VRAM, ...), and is evaluated
    once per preset. "backend" (e.g. a ComfyUI custom-node/model check) means
    the entry's answer depends on which specific backend of the engine is
    asked, and is evaluated once per enabled backend of that engine (see
    `evaluator.evaluate_preset_requirements_for_backends`). A plugin engine
    with several interchangeable backends (several ComfyUI servers, say)
    should mark its engine-specific checkers "backend" so routing
    (`GenerationOrchestrator`) and the admin requirements panel can tell
    which backends actually satisfy a preset.
    """

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
