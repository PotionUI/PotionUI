"""Typed documentation frontmatter (Docs 2.0).

A doc opts into a *type* via its frontmatter ``type:`` — ``technique`` or
``model`` today. Typed docs get a validated ``meta`` payload plus a cross-linked
``refs`` reverse index (techniques ↔ models by model family). Untyped docs (no
``type:``) are unaffected and keep behaving exactly as before.

Parsing is TOLERANT: unknown frontmatter keys are ignored (not an error) so a doc
never fails to render over a typo — the linter (``scripts/docs_lint.py``) is what
flags schema problems. This module owns the pydantic schemas, the tolerant
parse, and the pure reverse-index computation; the manager wires them in.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, ValidationError

# Enumerations the linter checks (parsing itself stays permissive).
TECHNIQUE_CATEGORY_GROUPS = ("Performance", "Quality", "Memory", "Sampling")
TECHNIQUE_STATUSES = ("stable", "experimental", "needs-gpu-validation")
KNOB_SURFACES = ("preset", "env", "admin")
MODEL_ENGINES = ("native", "diffusers")
ALL_NATIVE = "all-native"

TECHNIQUE = "technique"
MODEL = "model"


class Paper(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: Optional[str] = None
    arxiv: Optional[str] = None
    url: Optional[str] = None


class ReferenceImpl(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    url: str
    license: str


class Knob(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str
    surface: str
    default: Any = None
    effect: str = ""


class TechniqueMeta(BaseModel):
    """Frontmatter schema for ``type: technique``."""

    model_config = ConfigDict(extra="ignore")
    type: str
    title: str
    category_group: str
    status: str
    families: List[str] = []
    authors: List[str] = []
    paper: Optional[Paper] = None
    reference_impl: Optional[ReferenceImpl] = None
    knobs: List[Knob] = []
    related: List[str] = []


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    arch: str
    params: Optional[str] = None
    latent: str
    vae: str
    te: str
    guidance: str
    shift: Any = None
    # native → matches "all-native" techniques; diffusers → SDXL-style path.
    engine: str = "native"


class ModelFile(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: str
    dir: str
    note: Optional[str] = None


class ModelMeta(BaseModel):
    """Frontmatter schema for ``type: model``."""

    model_config = ConfigDict(extra="ignore")
    type: str
    title: str
    family_key: str
    modes: List[str] = []
    spec: ModelSpec
    files: List[ModelFile] = []


_SCHEMAS = {TECHNIQUE: TechniqueMeta, MODEL: ModelMeta}


def format_validation_errors(exc: ValidationError) -> List[str]:
    """Flatten a pydantic ValidationError into ``"field: message"`` strings."""
    out: List[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
        out.append(f"{loc}: {err.get('msg', 'invalid')}")
    return out


def parse_typed(frontmatter: Dict[str, Any]) -> Tuple[Optional[str], Optional[Dict[str, Any]], List[str]]:
    """Parse a doc's frontmatter into a typed ``meta`` dict.

    Returns ``(doc_type, meta_dict, errors)``:
      * ``doc_type`` — the ``type:`` value (``"technique"``/``"model"``/other/None).
      * ``meta_dict`` — the validated frontmatter as a plain dict when the type is
        recognised AND valid; ``None`` otherwise (untyped, unknown type, or invalid).
      * ``errors`` — validation error strings (empty for untyped/unknown/valid).

    Tolerant: unknown keys are ignored; an invalid typed doc returns its errors but
    still yields ``meta_dict=None`` so the caller renders the body regardless.
    """
    doc_type = frontmatter.get("type")
    schema = _SCHEMAS.get(doc_type) if isinstance(doc_type, str) else None
    if schema is None:
        return (doc_type if isinstance(doc_type, str) else None), None, []
    try:
        model = schema(**frontmatter)
    except ValidationError as exc:
        return doc_type, None, format_validation_errors(exc)
    return doc_type, model.model_dump(), []


# --- reverse index (pure) -----------------------------------------------------


def _technique_summary(doc_id: str, slug: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "slug": slug,
        "title": meta.get("title"),
        "category_group": meta.get("category_group"),
        "status": meta.get("status"),
        "doc_id": doc_id,
    }


def _model_summary(doc_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    return {"family_key": meta.get("family_key"), "title": meta.get("title"), "doc_id": doc_id}


def _model_matches_technique(model_meta: Dict[str, Any], technique_families: List[str]) -> bool:
    family = model_meta.get("family_key")
    engine = (model_meta.get("spec") or {}).get("engine", "native")
    return family in technique_families or (ALL_NATIVE in technique_families and engine == "native")


def refs_for(doc_type: Optional[str], meta: Optional[Dict[str, Any]], others: List[Tuple[str, str, str, Dict[str, Any]]]) -> Dict[str, Any]:
    """Cross-reference index for one typed doc.

    ``others`` is ``[(doc_type, doc_id, slug, meta), ...]`` for every OTHER typed
    doc. A model doc gets ``{"techniques": [...]}`` (techniques targeting its family
    or ``all-native`` when it's a native model); a technique doc gets
    ``{"models": [...]}`` (models in its family list, or all native models when it
    lists ``all-native``). Untyped/invalid docs get ``{}``.
    """
    if not meta:
        return {}
    if doc_type == MODEL:
        techs = [
            _technique_summary(o_id, o_slug, o_meta)
            for (o_type, o_id, o_slug, o_meta) in others
            if o_type == TECHNIQUE and _model_matches_technique(meta, o_meta.get("families", []))
        ]
        return {"techniques": techs}
    if doc_type == TECHNIQUE:
        families = meta.get("families", [])
        models = [
            _model_summary(o_id, o_meta)
            for (o_type, o_id, o_slug, o_meta) in others
            if o_type == MODEL and _model_matches_technique(o_meta, families)
        ]
        return {"models": models}
    return {}
