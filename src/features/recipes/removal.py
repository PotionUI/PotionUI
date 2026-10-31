from __future__ import annotations

import logging
import os
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Set, Tuple

from src.features.recipes.mode_plans import MODEL_REF_PREFIX, ModePlanner, PlanAssessment
from src.features.recipes.mode_slots import derive_mode_slots

logger = logging.getLogger(__name__)

SHARED = "shared"
ADMIN_DEFAULT = "admin_default"
OTHER_RECIPE = "other_recipe"
OUTSIDE = "outside_models_folder"
NOT_ON_DISK = "not_on_disk"


class ModeRemovalError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _names(labels: List[str]) -> str:
    if len(labels) <= 2:
        return " and ".join(labels)
    return f"{labels[0]} and {len(labels) - 1} other modes"


def _basename(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    return PurePosixPath(value.replace("\\", "/")).name.lower()


def _within(path: Path, root: Path) -> bool:
    try:
        real = Path(os.path.realpath(path))
        base = Path(os.path.realpath(root))
    except OSError:
        return False
    return real == base or base in real.parents


class ModeFileRemoval:
    def __init__(
        self,
        recipe_catalog: Any,
        planner: ModePlanner,
        model_repository: Any,
        model_locator: Any,
        model_roots: Any,
        preset_db_repo: Any = None,
        preset_loader: Any = None,
        locations_repository: Any = None,
    ):
        self.recipe_catalog = recipe_catalog
        self.planner = planner
        self.model_repository = model_repository
        self.model_locator = model_locator
        self.model_roots = model_roots
        self.preset_db_repo = preset_db_repo
        self.preset_loader = preset_loader
        self._locations_repository = locations_repository

    def _locations_repo(self):
        if self._locations_repository is None:
            from src.features.models.locations_repository import ModelLocationsRepository

            self._locations_repository = ModelLocationsRepository()
        return self._locations_repository

    def _assessment(self, recipe_id: str, mode_id: str) -> PlanAssessment:
        recipe = self.recipe_catalog.get_recipe(recipe_id) if self.recipe_catalog is not None else None
        step = next((s for s in (recipe.steps if recipe else []) if s.kind == "modes.plan"), None)
        if step is None:
            raise ModeRemovalError("not_found", "This recipe does not install modes.")
        assessment = self.planner.assess(
            step.params.get("preset_id"), hash_candidates="never", probe_remote=False, with_estimate=False, target="auto",
        )
        if assessment is None or assessment.mode(mode_id) is None:
            raise ModeRemovalError("not_found", f"'{mode_id}' is not a mode this recipe installs.")
        if assessment.target.kind != "local":
            raise ModeRemovalError("not_local", "Files can only be removed from this computer.")
        return assessment

    def _installed_presets(self) -> List[str]:
        if self.preset_db_repo is None:
            return []
        try:
            return [p.preset_id for p in self.preset_db_repo.get_all_installed_presets()]
        except Exception:
            return []

    def _overrides(self, preset_id: str) -> Dict[str, Any]:
        if self.preset_db_repo is None:
            return {}
        try:
            return self.preset_db_repo.get_preset_form_overrides(preset_id) or {}
        except Exception:
            return {}

    def _other_preset_files(self, preset_id: str) -> Dict[str, str]:
        used: Dict[str, str] = {}
        if self.preset_loader is None:
            return used
        for other_id in self._installed_presets():
            if other_id == preset_id:
                continue
            preset = self.preset_loader.load_preset_by_id(other_id)
            if preset is None:
                continue
            for slot in derive_mode_slots(preset).slots.values():
                for rec in slot.recommendations:
                    used.setdefault(rec.filename.lower(), preset.name)
        return used

    def _default_refs(self, preset_id: str, mode_id: str) -> Tuple[Set[str], Set[str]]:
        names: Set[str] = set()
        ids: Set[str] = set()
        for other_id in set(self._installed_presets()) | {preset_id}:
            for mode, fields in self._overrides(other_id).items():
                if other_id == preset_id and mode == mode_id:
                    continue
                for entry in (fields or {}).values():
                    value = (entry or {}).get("default") if isinstance(entry, dict) else None
                    if isinstance(value, str) and value.startswith(MODEL_REF_PREFIX):
                        ids.add(value[len(MODEL_REF_PREFIX):])
                    elif _basename(value):
                        names.add(_basename(value))
        return names, ids

    def _recipe_files(self) -> Dict[str, str]:
        files: Dict[str, str] = {}
        for recipe in self.recipe_catalog.list_recipes() if self.recipe_catalog is not None else []:
            for artifact in recipe.artifacts:
                for name in [artifact.filename] + [v.filename for v in artifact.variants]:
                    if name:
                        files.setdefault(name.lower(), recipe.name)
        return files

    def _write_root(self, model_type: str) -> Optional[Tuple[str, Path]]:
        try:
            entry = self.model_roots.write_dir(model_type)
        except Exception:
            return None
        return entry.root_id, Path(entry.path)

    def _local_paths(self, model: Any) -> Tuple[List[Tuple[Any, Path]], bool]:
        root = self._write_root(model.model_type)
        inside: List[Tuple[Any, Path]] = []
        outside = False
        for location in self.model_locator.locations(model.id):
            if location.status != "present" or location.path is None:
                continue
            path = Path(location.path)
            if root is not None and location.root_id == root[0] and not path.is_symlink() and _within(path, root[1]):
                inside.append((location, path))
            else:
                outside = True
        return inside, outside

    def preview(self, recipe_id: str, mode_id: str) -> Dict[str, Any]:
        assessment = self._assessment(recipe_id, mode_id)
        mode = assessment.mode(mode_id)
        preset_id = assessment.slot_plan.preset_id
        slot_ids = list(dict.fromkeys(list(mode.slot_ids) + list(mode.extra_slot_ids)))
        others = {
            m: self.planner.mode_ready(assessment, m) for m in assessment.offered if m != mode_id
        }
        other_presets = self._other_preset_files(preset_id)
        default_names, default_ids = self._default_refs(preset_id, mode_id)
        recipe_files = {
            name: label for name, label in self._recipe_files().items()
            if label != getattr(self.recipe_catalog.get_recipe(recipe_id), "name", None)
        }
        delete: List[Dict[str, Any]] = []
        keep: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for slot_id in slot_ids:
            state = assessment.slots[slot_id]
            for variant_id, model in state.installed.items():
                model_id = getattr(model, "id", None)
                if not model_id or model_id in seen:
                    continue
                seen.add(model_id)
                variant = state.variant(variant_id) or {}
                entry = {
                    "model_id": model_id,
                    "label": state.slot.label,
                    "filename": getattr(model, "filename", None) or variant.get("filename"),
                    "model_type": state.slot.model_type,
                    "size_bytes": getattr(model, "file_size", None) or variant.get("size_bytes"),
                }
                name = (entry["filename"] or "").lower()
                users = [m for m in state.slot.used_by if m != mode_id and others.get(m)]
                reason = None
                if users:
                    labels = [assessment.mode(m).label for m in users]
                    reason = (SHARED, f"Used by {_names(labels)}" if len(users) > 1 else f"Also used by {labels[0]}", users)
                elif name in other_presets:
                    reason = (SHARED, f"Also used by {other_presets[name]}", [])
                elif name in default_names or model_id in default_ids:
                    reason = (ADMIN_DEFAULT, "Set as a default file by an admin", [])
                elif name in recipe_files:
                    reason = (OTHER_RECIPE, f"Part of the {recipe_files[name]} recipe", [])
                else:
                    inside, outside = self._local_paths(model)
                    if not inside:
                        reason = (
                            (OUTSIDE, "You added this yourself, outside the models folder", [])
                            if outside
                            else (NOT_ON_DISK, "Not found on this computer", [])
                        )
                if reason is None:
                    delete.append(entry)
                else:
                    keep.append({**entry, "reason_code": reason[0], "reason": reason[1], "modes": reason[2]})
        deleted_names = {(d["filename"] or "").lower() for d in delete}
        clears = []
        for field_name, entry in (self._overrides(preset_id).get(mode_id) or {}).items():
            value = (entry or {}).get("default") if isinstance(entry, dict) else None
            if _basename(value) in deleted_names:
                clears.append({"field": field_name})
        return {
            "preset_id": preset_id,
            "mode": mode_id,
            "label": mode.label,
            "target": assessment.target.key,
            "delete": delete,
            "keep": keep,
            "frees_bytes": sum(d["size_bytes"] or 0 for d in delete),
            "clears_defaults": clears,
        }

    def remove(self, recipe_id: str, mode_id: str, model_ids: List[str]) -> Dict[str, Any]:
        preview = self.preview(recipe_id, mode_id)
        wanted = set(model_ids or [])
        chosen = [entry for entry in preview["delete"] if entry["model_id"] in wanted]
        if not chosen:
            raise ModeRemovalError("conflict", "None of these files can be removed any more.")
        deleted = []
        freed = 0
        for entry in chosen:
            model = self.model_repository.get_by_id(entry["model_id"])
            if model is None:
                continue
            inside, _ = self._local_paths(model)
            removed_any = False
            for location, path in inside:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    logger.warning("Removing '%s' failed: %s", path, exc)
                    continue
                removed_any = True
                try:
                    self._locations_repo().delete(location.id)
                except Exception:
                    logger.exception("Dropping the location row of '%s' failed", path)
            if not removed_any:
                continue
            remaining = [loc for loc in self.model_locator.locations(model.id) if loc.status == "present"]
            if not remaining:
                self.model_repository.delete(model.id)
            deleted.append(entry)
            freed += entry["size_bytes"] or 0
        self._clear_defaults(preview["preset_id"], mode_id, {(d["filename"] or "").lower() for d in deleted})
        return {"deleted": deleted, "kept": preview["keep"], "freed_bytes": freed}

    def _clear_defaults(self, preset_id: str, mode_id: str, deleted_names: Set[str]) -> None:
        if self.preset_db_repo is None or not deleted_names:
            return
        stored = self._overrides(preset_id)
        fields = stored.get(mode_id)
        if not fields:
            return
        changed = False
        for field_name, entry in list(fields.items()):
            if isinstance(entry, dict) and _basename(entry.get("default")) in deleted_names:
                remainder = {k: v for k, v in entry.items() if k != "default"}
                if remainder:
                    fields[field_name] = remainder
                else:
                    fields.pop(field_name)
                changed = True
        if changed:
            if not fields:
                stored.pop(mode_id, None)
            self.preset_db_repo.set_preset_form_overrides(preset_id, stored)
