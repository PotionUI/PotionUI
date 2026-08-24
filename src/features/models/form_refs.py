"""
Turn the picker's model references into the string a backend actually wants.

The picker stores `model:<model_id>` rather than a path. The prefix makes the value
self-describing, so form data can be walked generically - no form schema, no field-type
plumbing, and nested shapes like the LoRA picker's `[{model, strength}, ...]` are handled
by the same recursion.

At generation time the selected backend is known, so each reference is rewritten to that
backend's `ref`: `models/loras/x.safetensors` for native, `style/x.safetensors` for a
ComfyUI server. Preset templates therefore receive an engine-native string and need no
`replace(...)` surgery.

Anything that is not a `model:` reference passes through untouched. That is what keeps
saved sessions, preset defaults (bare filenames) and legacy path values working.

See docs/models.md.
"""

from typing import Any, Dict, List

from src.platform.observability.logger import logger
from src.features.models.availability_repository import (
    model_availability_repo,
)


MODEL_REF_PREFIX = "model:"

_warned_unindexed_backend: set = set()


class ModelRefNotAvailableError(RuntimeError):
    """The selected backend has no availability row for a referenced model."""


class ModelDigestConflictError(RuntimeError):
    """The selected backend's copy of a referenced model does not match the expected digest.

    `candidate_backends`/`backends_holding` already exclude a conflicted backend from
    selection, so this is the last-resort block: it only fires if a conflicted backend
    was somehow still chosen (a single-backend engine, or narrowing skipped because
    nothing has been indexed yet). Raised rather than silently falling back, because
    generating against the wrong bytes produces an output nobody can tell is wrong.
    """


def is_model_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(MODEL_REF_PREFIX)


def make_model_ref(model_id: str) -> str:
    return f"{MODEL_REF_PREFIX}{model_id}"


def model_id_of(value: str) -> str:
    return value[len(MODEL_REF_PREFIX):]


def collect_model_ids(form_data: Any) -> List[str]:
    """Every model referenced anywhere in the form, in first-seen order.

    Order is stable so error messages and backend narrowing are deterministic.
    """
    found: List[str] = []
    seen = set()

    def walk(node: Any) -> None:
        if is_model_ref(node):
            model_id = model_id_of(node)
            if model_id and model_id not in seen:
                seen.add(model_id)
                found.append(model_id)
        elif isinstance(node, dict):
            for item in node.values():
                walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(form_data)
    return found


def resolve_form_model_refs(form_data: Any, backend_id: str) -> Any:
    """Rewrite every `model:<id>` into the ref this backend needs.

    Raises rather than passing an unresolvable reference through: handing a raw
    `model:<ulid>` to a pipeline would surface as an opaque failure deep inside the
    engine, long after the point where we knew exactly what was wrong.

    The exception is a backend that has never been indexed. It holds models; it has
    simply never been asked, so its lack of an availability row says nothing. Falling
    back to the model's own path or filename reproduces exactly what the picker used to
    submit, which keeps generation working before the first index run.

    A row whose digest conflicts with the model's canonical one raises immediately,
    ahead of the missing/fallback handling below: `candidate_backends` already steers
    selection away from a conflicted backend, so reaching this row at all means
    something bypassed that (a single-backend engine, most likely) - and generating
    against the wrong bytes is worse than refusing outright. See
    ModelDigestConflictError.
    """
    model_ids = collect_model_ids(form_data)
    if not model_ids:
        return form_data

    indexed = model_availability_repo.any_indexed([backend_id])

    refs: Dict[str, str] = {}
    missing: List[str] = []
    for model_id in model_ids:
        row = model_availability_repo.get(model_id, backend_id)
        if row is not None and row.confidence == "conflict":
            raise ModelDigestConflictError(_describe_conflict(model_id, row))
        if row is not None:
            refs[model_id] = row.ref
        elif indexed:
            missing.append(model_id)
        else:
            fallback = _fallback_ref(model_id)
            if fallback is None:
                missing.append(model_id)
            else:
                refs[model_id] = fallback

    if missing:
        if indexed:
            raise ModelRefNotAvailableError(
                f"Backend '{backend_id}' cannot load {len(missing)} selected model(s): "
                f"{', '.join(_describe(missing))}. Re-index the backend, or pick models it has."
            )
        raise ModelRefNotAvailableError(
            f"{len(missing)} selected model(s) no longer exist: {', '.join(missing)}."
        )

    if not indexed and backend_id not in _warned_unindexed_backend:
        logger.warning(
            f"Backend '{backend_id}' has never been indexed; resolved "
            f"{len(refs)} model reference(s) from the model index instead. "
            f"Index the backend so generations can be routed by availability."
        )
        _warned_unindexed_backend.add(backend_id)

    def rewrite(node: Any) -> Any:
        if is_model_ref(node):
            return refs[model_id_of(node)]
        if isinstance(node, dict):
            return {key: rewrite(value) for key, value in node.items()}
        if isinstance(node, list):
            return [rewrite(item) for item in node]
        if isinstance(node, tuple):
            return tuple(rewrite(item) for item in node)
        return node

    resolved = rewrite(form_data)
    logger.debug(
        f"[FORM_REFS] Resolved {len(refs)} model reference(s) for backend '{backend_id}'"
    )
    return resolved


def _describe_conflict(model_id: str, row) -> str:
    """Name the model, the backend, and both digests - enough to act on without
    reading code. `row.digest` is what THIS backend computed; the expected value is
    the model's own canonical `models.sha256`, fetched fresh rather than trusted from
    the row so the message reflects the current record even if it moved."""
    from src.features.models.repository import model_repo

    try:
        model = model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
    except Exception:
        model = None

    name = model.filename if model else model_id
    expected = (model.sha256[:12] + "…") if model and model.sha256 else "unknown"
    found = (row.digest[:12] + "…") if row.digest else "unknown"
    return (
        f"Backend '{row.backend_id}' cannot be used for model '{name}': its copy does "
        f"not match the expected file (expected digest {expected}, found {found}). "
        f"Re-sync or replace the file on that backend, then re-index it."
    )


def _fallback_ref(model_id: str) -> Any:
    """What the picker would have submitted before availability existed.

    `file_path` for a model on this host; the bare filename otherwise, which is what a
    ComfyUI server resolves against its own folders anyway.
    """
    from src.features.models.repository import model_repo

    try:
        model = model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
    except Exception:
        return None
    if not model:
        return None
    return model.file_path or model.filename


def _describe(model_ids: List[str]) -> List[str]:
    """Prefer filenames over ULIDs; an error naming `detail.safetensors` is actionable."""
    from src.features.models.repository import model_repo

    described = []
    for model_id in model_ids:
        try:
            model = model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
            described.append(model.filename if model else model_id)
        except Exception:
            described.append(model_id)
    return described
