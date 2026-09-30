from __future__ import annotations

from .components import Components, Verdict, decide
from .reader import (
    HeaderResult,
    HeaderStatus,
    HeaderView,
    TensorInfo,
    read_header,
    read_safetensors_header,
)
from .registry import (
    ClassifierMatch,
    ModelClassifierDefinition,
    ModelClassifierRegistry,
    model_classifier_registry,
)
from .signatures import FamilyMatch


def classify_header(view: HeaderView, registry: ModelClassifierRegistry | None = None) -> Verdict:
    active = registry or model_classifier_registry
    found = active.classify(view)
    return decide(
        view,
        found.match if found else None,
        found.key if found else None,
        active.fingerprint(),
    )


__all__ = [
    "ClassifierMatch",
    "Components",
    "FamilyMatch",
    "HeaderResult",
    "HeaderStatus",
    "HeaderView",
    "ModelClassifierDefinition",
    "ModelClassifierRegistry",
    "TensorInfo",
    "Verdict",
    "classify_header",
    "model_classifier_registry",
    "read_header",
    "read_safetensors_header",
]
