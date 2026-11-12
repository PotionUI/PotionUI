from typing import Any, Dict, Iterable, List, Optional

from src.features.models.recommendations import find_installed_model
from src.features.presets.requirements.builtin import ModelRequirementChecker
from src.features.presets.requirements.evaluator import describe_requirement_entry
from src.platform.plugins.requirement_checkers import RequirementCheckerRegistry


def _group_assigned(repo: Any, digests: Any, group: Dict[str, Any], form_overrides: Optional[Dict[str, Any]], allowed: set) -> bool:
    for candidate in group.get("files") or []:
        model = find_installed_model(
            repo,
            candidate.get("model_type") or "",
            candidate.get("filename") or "",
            sha256=candidate.get("sha256"),
            size_bytes=candidate.get("size_bytes"),
            digests=digests,
        )
        if model is not None and getattr(model, "is_available", True) and model.id in allowed:
            return True
    default = ModelRequirementChecker.admin_default_model(repo, form_overrides, group)
    return default is not None and default.id in allowed


def model_requirement_assigned(spec: Dict[str, Any], repo: Any, digests: Any, form_overrides: Optional[Dict[str, Any]], allowed: Iterable[str]) -> bool:
    allowed_ids = set(allowed)
    return any(_group_assigned(repo, digests, group, form_overrides, allowed_ids) for group in spec.get("candidates") or [])


def user_missing_by_mode(
    registry: RequirementCheckerRegistry,
    specs: List[Dict[str, Any]],
    repo: Any,
    digests: Any,
    form_overrides: Optional[Dict[str, Any]],
    allowed: Iterable[str],
) -> Dict[str, List[str]]:
    allowed_ids = set(allowed)
    by_mode: Dict[str, List[str]] = {}
    for spec in specs:
        mode = spec.get("mode")
        if not mode or spec.get("type") != "model" or spec.get("optional") or not spec.get("candidates"):
            continue
        missing = by_mode.setdefault(mode, [])
        if not model_requirement_assigned(spec, repo, digests, form_overrides, allowed_ids):
            missing.append(describe_requirement_entry(registry, spec))
    return by_mode
