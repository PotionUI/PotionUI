import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.features.models.availability import PACKAGING_FULL_CHECKPOINT
from src.features.models.exceptions import (
    InvalidModelTypeException,
    ModelNotFoundException,
    ModelTypeConflictException,
    ModelTypeNotAssignableException,
)
from src.features.models.type_repository import ModelTypeRepository
from src.features.models.type_resolution import folder_type_of
from src.platform.database.rows import now_iso
from src.platform.filesystem.model_types import CHECKPOINT_MODEL_TYPE, MODEL_TYPES

SOURCE_ADMIN = "admin"
SOURCE_RECIPE = "recipe"
SOURCE_DOWNLOAD = "download"

NOT_ASSIGNABLE_TYPES = frozenset({"llm"})


@dataclass(frozen=True)
class AssertionOutcome:
    applied: bool
    model_id: Optional[str] = None
    conflicts: List[Dict[str, Any]] = field(default_factory=list)


def assignable_types() -> List[str]:
    return [t for t in MODEL_TYPES if t not in NOT_ASSIGNABLE_TYPES]


def build_type_info(
    model: Any,
    copies: Sequence[Dict[str, Any]],
    verdict: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    folder_types = [c["model_type"] for c in copies]
    components: List[str] = []
    if verdict and verdict.get("components"):
        try:
            components = list(json.loads(verdict["components"]))
        except (TypeError, ValueError):
            components = []
    extractable = bool(verdict and verdict.get("transformer_extractable"))
    return {
        "source": model.type_source,
        "folder_type": folder_type_of(folder_types) if folder_types else None,
        "family": verdict["family"] if verdict else None,
        "variant": verdict["variant"] if verdict else None,
        "classifier": verdict["classifier"] if verdict else None,
        "components": components,
        "verdict_status": verdict["status"] if verdict else None,
        "packaging": (
            PACKAGING_FULL_CHECKPOINT if model.model_type == CHECKPOINT_MODEL_TYPE and extractable else None
        ),
    }


class ModelTypeManager:

    def __init__(
        self,
        model_repository: Any,
        types_repository: ModelTypeRepository,
        recompute: Callable[[Sequence[str]], List[Dict[str, Any]]],
    ):
        self._models = model_repository
        self._types = types_repository
        self._recompute = recompute

    def assert_type(
        self, sha256: str, model_type: str, source: str, set_by: Optional[str] = None
    ) -> AssertionOutcome:
        previous = self._types.get_assertion_rows(sha256).get(source)
        self._types.put_assertion(sha256, model_type, source, set_by, now_iso())
        return self._resolve(sha256, source, previous)

    def reset_type(self, sha256: str, source: str = SOURCE_ADMIN) -> AssertionOutcome:
        previous = self._types.get_assertion_rows(sha256).get(source)
        if previous is None:
            return AssertionOutcome(False)
        self._types.delete_assertion(sha256, source)
        return self._resolve(sha256, source, previous)

    def _resolve(self, sha256: str, source: str, previous: Optional[Dict[str, Any]]) -> AssertionOutcome:
        model = self._models.get_by_sha256(sha256, include_providers=False)
        if model is None:
            return AssertionOutcome(True)
        try:
            conflicts = self._recompute([model.id])
        except Exception:
            self._restore(sha256, source, previous)
            raise
        if conflicts:
            self._restore(sha256, source, previous)
        return AssertionOutcome(not conflicts, model.id, conflicts)

    def _restore(self, sha256: str, source: str, previous: Optional[Dict[str, Any]]) -> None:
        if previous is None:
            self._types.delete_assertion(sha256, source)
        else:
            self._types.put_assertion(
                sha256, previous["model_type"], previous["source"], previous["set_by"], previous["set_at"]
            )

    def set_admin_type(self, model_id: str, model_type: str, user_id: Optional[str]) -> None:
        if model_type not in assignable_types():
            raise InvalidModelTypeException(f"'{model_type}' is not a type a model can be set to")
        model = self._models.get_by_id(model_id, include_providers=False, include_tags=False)
        if model is None:
            raise ModelNotFoundException(f"Model '{model_id}' not found")
        if model.is_directory:
            raise ModelTypeNotAssignableException("Directory models keep the type of their folder")
        if not model.sha256:
            raise ModelTypeNotAssignableException("This model has no content hash yet, so its type cannot be set")
        self._raise_on_collision(model, model_type)
        outcome = self.assert_type(model.sha256, model_type, SOURCE_ADMIN, user_id)
        if outcome.conflicts:
            raise self._conflict(model, model_type)

    def reset_admin_type(self, model_id: str) -> None:
        model = self._models.get_by_id(model_id, include_providers=False, include_tags=False)
        if model is None:
            raise ModelNotFoundException(f"Model '{model_id}' not found")
        if not model.sha256:
            raise ModelTypeNotAssignableException("This model has no content hash yet, so it has no set type")
        outcome = self.reset_type(model.sha256)
        if outcome.conflicts:
            raise ModelTypeConflictException(
                f"'{model.filename}' cannot return to its automatic type: another model already uses that name there"
            )

    def _raise_on_collision(self, model: Any, model_type: str) -> None:
        if model_type == model.model_type:
            return
        holder = self._types.identities_for_filenames([model.filename]).get((model_type, model.filename))
        if holder is not None and holder != model.id:
            raise self._conflict(model, model_type, holder)

    def _conflict(self, model: Any, model_type: str, holder: Optional[str] = None) -> ModelTypeConflictException:
        if holder is None:
            holder = self._types.identities_for_filenames([model.filename]).get((model_type, model.filename), "")
        return ModelTypeConflictException(
            f"'{model.filename}' already exists as {model_type} (model {holder})", holder
        )
