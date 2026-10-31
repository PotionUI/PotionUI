from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from src.features.models.recommendations import format_size
from src.features.recipes.executors._artifact_lookup import find_artifact_model, same_size_candidates
from src.features.recipes.executors._provider_credentials import (
    credential_prompts,
    gated_warnings,
    resolve_provider_registry,
)
from src.features.recipes.mode_slots import DerivedSlot, ModeSlotPlan, PlanApprovalError, derive_mode_slots
from src.features.recipes.targets import InstallTargets, TargetOption, TargetProfile
from src.features.recipes.variants import choose_variant, describe_variant
from src.platform.runtime.gpu_profile import GpuProfile

logger = logging.getLogger(__name__)

MODEL_REF_PREFIX = "model:"
_SUMMARY_TTL_SECONDS = 5.0


@dataclass
class SlotState:
    slot: DerivedSlot
    variants: List[Dict[str, Any]]
    installed: Dict[str, Any]
    recommended_variant_id: Optional[str]
    reason: str
    best_fit_variant_id: Optional[str] = None
    satisfied_by: Optional[str] = None
    hidden: bool = False
    manual: bool = False

    @property
    def is_installed(self) -> bool:
        return bool(self.installed) or self.satisfied_by is not None

    def variant(self, variant_id: Optional[str]) -> Optional[Dict[str, Any]]:
        for entry in self.variants:
            if entry["id"] == variant_id:
                return entry
        return None


@dataclass
class PlanAssessment:
    slot_plan: ModeSlotPlan
    target: TargetOption
    options: List[TargetOption]
    profile: TargetProfile
    slots: Dict[str, SlotState]
    offered: List[str]
    selected: List[str]
    locked: List[str]
    extras_offered: bool = True
    preselect_extras: List[str] = field(default_factory=list)
    estimate: Optional[Dict[str, Any]] = None

    @property
    def gpu(self) -> GpuProfile:
        return self.profile.gpu

    def mode(self, mode_id: str):
        return self.slot_plan.modes.get(mode_id)


def _default_basename(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    return PurePosixPath(value.replace("\\", "/")).name


class ModePlanner:
    def __init__(
        self,
        preset_loader: Any,
        model_repository: Any,
        preset_db_repo: Any = None,
        targets: Optional[InstallTargets] = None,
        digests: Any = None,
        type_manager: Any = None,
        provider_registry_factory: Any = None,
        availability_repository: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.preset_loader = preset_loader
        self.model_repository = model_repository
        self.preset_db_repo = preset_db_repo
        self.targets = targets or InstallTargets()
        self.digests = digests
        self.type_manager = type_manager
        self.provider_registry_factory = provider_registry_factory
        self.availability_repository = availability_repository
        self._clock = clock
        self._summaries: Dict[Tuple, Tuple[float, Dict[str, Any]]] = {}

    def _provider_registry(self):
        return resolve_provider_registry(self.provider_registry_factory)

    def slot_plan(self, preset_id: str, modes: Optional[Iterable[str]] = None) -> Optional[ModeSlotPlan]:
        preset = self.preset_loader.load_preset_by_id(preset_id) if self.preset_loader is not None else None
        if preset is None:
            return None
        return derive_mode_slots(preset, modes)

    def _overrides(self, preset_id: str) -> Dict[str, Dict[str, Any]]:
        if self.preset_db_repo is None:
            return {}
        try:
            return self.preset_db_repo.get_preset_form_overrides(preset_id) or {}
        except Exception:
            logger.exception("Reading form overrides for '%s' failed", preset_id)
            return {}

    def _model_available(self, value: Any, model_type: str) -> bool:
        if isinstance(value, str) and value.startswith(MODEL_REF_PREFIX):
            model = self.model_repository.get_by_id(value[len(MODEL_REF_PREFIX):])
            return model is not None and getattr(model, "is_available", True)
        name = _default_basename(value)
        if not name:
            return False
        model = self.model_repository.get_by_identity(model_type, name)
        if model is None:
            matches = self.model_repository.get_by_filename(name) or []
            model = next((m for m in matches if getattr(m, "is_available", True)), None)
        return model is not None and getattr(model, "is_available", True)

    def model_available(self, value: Any, model_type: str) -> bool:
        try:
            return self._model_available(value, model_type)
        except Exception:
            return False

    def _on_target(self, model: Any, profile: TargetProfile) -> bool:
        destination = profile.destination_backend_id
        if model is None or destination is None or self.availability_repository is None:
            return model is not None
        model_id = getattr(model, "id", None)
        if not model_id:
            return True
        try:
            return self.availability_repository.get(model_id, destination) is not None
        except Exception:
            return True

    def _slot_override(self, slot: DerivedSlot, overrides: Dict[str, Dict[str, Any]]) -> Tuple[Optional[Any], bool]:
        default = None
        hidden = False
        for mode_id in slot.used_by:
            field_name = slot.field_for(mode_id)
            entry = (overrides.get(mode_id) or {}).get(field_name) or {}
            if not isinstance(entry, dict):
                continue
            if default is None and entry.get("default") not in (None, ""):
                default = entry.get("default")
            if entry.get("visible") is False:
                hidden = True
        return default, hidden

    def _slot_state(
        self,
        slot: DerivedSlot,
        profile: TargetProfile,
        overrides: Dict[str, Dict[str, Any]],
        hash_candidates: str,
    ) -> SlotState:
        override_default, hidden = self._slot_override(slot, overrides)
        if slot.manual or not profile.downloads:
            satisfied = "admin_default" if override_default is not None and self.model_available(override_default, slot.model_type) else None
            return SlotState(
                slot=slot,
                variants=[],
                installed={},
                recommended_variant_id=None,
                reason="Pick your own file in Models" if slot.manual else f"Add this file to {profile.option.label}'s models folder",
                satisfied_by=satisfied,
                hidden=hidden,
                manual=True,
            )
        artifact = slot.artifact
        gpu = profile.gpu
        variants = list(artifact.variants)
        default_name = _default_basename(override_default)
        named = next((v for v in variants if default_name and v.filename.lower() == default_name.lower()), None)
        if hidden and named is not None:
            variants = [named]
        installed: Dict[str, Any] = {}
        for variant in variants:
            model = find_artifact_model(
                self.model_repository, artifact.resolve(variant.id), self.type_manager, self.digests, hash_candidates
            )
            if model is not None and self._on_target(model, profile):
                installed[variant.id] = model
        restricted = artifact if len(variants) == len(artifact.variants) else _restrict(artifact, variants)
        ideal = choose_variant(restricted, gpu)
        choice = choose_variant(restricted, gpu, installed.keys())
        recommended = choice.variant_id
        reason = choice.reason
        if named is not None and not installed:
            recommended, reason = named.id, "The file an admin set as this field's default"
        satisfied = None
        if not installed and override_default is not None and named is None and self.model_available(override_default, slot.model_type):
            satisfied = "admin_default"
        described = []
        for variant in variants:
            entry = describe_variant(variant, gpu, installed=variant.id in installed)
            entry["best_fit"] = variant.id == ideal.variant_id
            described.append(entry)
        return SlotState(
            slot=slot,
            variants=described,
            installed=installed,
            recommended_variant_id=recommended,
            reason=reason,
            best_fit_variant_id=ideal.variant_id,
            satisfied_by=satisfied,
            hidden=hidden,
        )

    def assess(
        self,
        preset_id: str,
        *,
        modes: Any = "all",
        selected: Sequence[str] = (),
        locked: Sequence[str] = (),
        preselect: Sequence[str] = (),
        extras: str = "offer",
        preselect_extras: Sequence[str] = (),
        target: Optional[str] = None,
        hash_candidates: str = "background",
        probe_remote: bool = True,
        with_estimate: bool = True,
    ) -> Optional[PlanAssessment]:
        preset = self.preset_loader.load_preset_by_id(preset_id) if self.preset_loader is not None else None
        if preset is None:
            return None
        wanted = None if modes in (None, "all") else [m for m in modes if m in (preset.modes or {})]
        slot_plan = derive_mode_slots(preset, wanted)
        option, options = self.targets.resolve(slot_plan.engine, target)
        if option is None:
            raise PlanApprovalError(
                f"'{target}' is not a place this preset can install to." if target else "There is nowhere to install this preset's files yet."
            )
        model_types = {slot.model_type for slot in slot_plan.slots.values()}
        profile = self.targets.profile(option, model_types, probe_remote=probe_remote)
        overrides = self._overrides(preset_id)
        slots = {
            slot_id: self._slot_state(slot, profile, overrides, hash_candidates)
            for slot_id, slot in slot_plan.slots.items()
        }
        offered = list(slot_plan.modes)
        chosen = [m for m in list(selected) + list(locked) + list(preselect) if m in slot_plan.modes]
        extras_offered = extras != "skip"
        return PlanAssessment(
            slot_plan=slot_plan,
            target=option,
            options=options,
            profile=profile,
            slots=slots,
            offered=offered,
            selected=list(dict.fromkeys(chosen)),
            locked=[m for m in locked if m in slot_plan.modes],
            extras_offered=extras_offered,
            preselect_extras=[s for s in preselect_extras if extras_offered and s in slots and not slots[s].manual],
            estimate=self.targets.estimate(option) if with_estimate else None,
        )

    def _chosen_variant(self, state: SlotState, selections: Dict[str, str]) -> Optional[Dict[str, Any]]:
        picked = selections.get(state.slot.id)
        return state.variant(picked) or state.variant(state.recommended_variant_id)

    def _needs_download(self, state: SlotState, selections: Dict[str, str]) -> bool:
        if state.manual or state.satisfied_by is not None:
            return False
        chosen = self._chosen_variant(state, selections)
        return chosen is not None and not chosen.get("installed")

    def mode_ready(self, assessment: PlanAssessment, mode_id: str) -> bool:
        mode = assessment.mode(mode_id)
        if mode is None:
            return False
        for slot_id in mode.slot_ids:
            state = assessment.slots[slot_id]
            if not state.manual and not state.is_installed:
                return False
        for group in mode.any_of:
            if not any(assessment.slots[sid].is_installed for sid in group):
                return False
        return True

    def needed_slots(self, assessment: PlanAssessment, modes: Iterable[str], extras: Iterable[str]) -> List[str]:
        needed: List[str] = []
        extras = list(extras)
        for mode_id in modes:
            mode = assessment.mode(mode_id)
            if mode is None:
                continue
            needed.extend(mode.slot_ids)
            for group in mode.any_of:
                if any(assessment.slots[sid].is_installed or sid in extras for sid in group):
                    continue
                downloadable = [sid for sid in group if not assessment.slots[sid].manual]
                if downloadable:
                    needed.append(downloadable[0])
        needed.extend(extras)
        return list(dict.fromkeys(needed))

    def file_entry(self, state: SlotState, variant: Dict[str, Any]) -> Dict[str, Any]:
        resolved = state.slot.artifact.resolve(variant["id"])
        label = state.slot.label
        if len(state.slot.artifact.variants) > 1 and variant.get("label"):
            label = f"{label} ({variant['label']})"
        return {
            "slot_id": state.slot.id,
            "variant_id": variant["id"],
            "filename": resolved.filename,
            "model_type": resolved.model_type,
            "size_bytes": resolved.size_bytes,
            "sha256": resolved.checksum.value if resolved.checksum else None,
            "provider_hint": dict(resolved.provider_hint or {}),
            "display_name": label,
            "gated": resolved.gated,
            "license_url": resolved.license_url,
        }

    def selection_files(
        self,
        assessment: PlanAssessment,
        modes: Iterable[str],
        extras: Iterable[str],
        selections: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        selections = selections or {}
        files = []
        for slot_id in self.needed_slots(assessment, modes, extras):
            state = assessment.slots.get(slot_id)
            if state is None or not self._needs_download(state, selections):
                continue
            files.append(self.file_entry(state, self._chosen_variant(state, selections)))
        return files

    def mode_missing_bytes(self, assessment: PlanAssessment, mode_id: str) -> Optional[int]:
        files = self.selection_files(assessment, [mode_id], [])
        sizes = [f["size_bytes"] for f in files]
        if any(size is None for size in sizes):
            return None
        return sum(sizes)

    def disk_shortfalls(self, assessment: PlanAssessment, files: Sequence[Dict[str, Any]]) -> List[str]:
        profile = assessment.profile
        if not profile.downloads:
            return []
        by_disk: Dict[str, int] = {}
        volumes = {}
        for entry in files:
            disk = profile.disk_for(entry["model_type"])
            if disk is None or disk.usable_bytes is None:
                continue
            volumes[disk.id] = disk
            by_disk[disk.id] = by_disk.get(disk.id, 0) + (entry.get("size_bytes") or 0)
        problems = []
        for disk_id, needed in by_disk.items():
            disk = volumes[disk_id]
            if needed > disk.usable_bytes:
                where = assessment.target.label if profile.destination_backend_id else disk.label.lower()
                problems.append(
                    f"Not enough space on {where}: you picked {format_size(needed)}, but only "
                    f"{format_size(disk.usable_bytes)} can be used ({format_size(disk.free_bytes)} is free and "
                    f"{format_size(disk.reserve_bytes)} is kept spare). Deselect about "
                    f"{format_size(needed - disk.usable_bytes)} or install somewhere else."
                )
        return problems

    def _slot_view(self, assessment: PlanAssessment, state: SlotState) -> Dict[str, Any]:
        slot = state.slot
        disk = assessment.profile.disk_for(slot.model_type)
        return {
            "id": slot.id,
            "label": slot.label,
            "field": slot.field_for(slot.used_by[0]) if slot.used_by else None,
            "kind": slot.model_type,
            "model_type": slot.model_type,
            "required": slot.required,
            "extra": slot.extra,
            "manual": state.manual,
            "used_by": list(slot.used_by),
            "disk_id": disk.id if disk is not None else None,
            "satisfied_by": state.satisfied_by,
            "variants": state.variants,
            "recommended_variant_id": state.recommended_variant_id,
            "best_fit_variant_id": state.best_fit_variant_id,
            "reason": state.reason,
        }

    def _option_view(self, assessment: PlanAssessment, option: TargetOption) -> Dict[str, Any]:
        profile = assessment.profile if option == assessment.target else None
        if profile is None and option.kind != "external":
            try:
                profile = self.targets.profile(option, {s.model_type for s in assessment.slot_plan.slots.values()}, probe_remote=False)
            except Exception:
                profile = None
        gpu = profile.gpu if profile is not None else GpuProfile()
        return {
            "backend_id": option.key,
            "label": option.label,
            "kind": option.kind,
            "free_bytes": profile.free_bytes if profile is not None else None,
            "gpu_name": gpu.name if gpu.has_gpu else None,
            "vram_gb": gpu.vram_gb if gpu.has_gpu else None,
        }

    def default_modes(self, assessment: PlanAssessment) -> List[str]:
        return list(assessment.selected)

    def consent_payload(self, assessment: PlanAssessment) -> Dict[str, Any]:
        visible_slots = [
            state for state in assessment.slots.values()
            if assessment.extras_offered or not state.slot.extra
        ]
        default_modes = self.default_modes(assessment)
        default_files = self.selection_files(assessment, default_modes, assessment.preselect_extras)
        sizes = [f["size_bytes"] for f in default_files]
        modes_view = []
        for mode_id in assessment.offered:
            mode = assessment.mode(mode_id)
            modes_view.append(
                {
                    "id": mode_id,
                    "label": mode.label,
                    "short_description": mode.short_description,
                    "selected": mode_id in default_modes,
                    "locked": mode_id in assessment.locked,
                    "ready": self.mode_ready(assessment, mode_id),
                    "slot_ids": list(mode.slot_ids),
                    "extra_slot_ids": list(mode.extra_slot_ids) if assessment.extras_offered else [],
                    "any_of": [list(group) for group in mode.any_of],
                    "missing_bytes": self.mode_missing_bytes(assessment, mode_id),
                }
            )
        manifest = []
        for state in visible_slots:
            if state.manual:
                continue
            for variant in state.variants:
                if not variant.get("installed"):
                    manifest.append(self.file_entry(state, variant))
        offered_missing = [
            state.slot.artifact.resolve(self._chosen_variant(state, {})["id"])
            for state in visible_slots
            if not state.manual and not state.is_installed and self._chosen_variant(state, {}) is not None
        ]
        checking = []
        for entry in default_files:
            state = assessment.slots[entry["slot_id"]]
            candidates = same_size_candidates(state.slot.artifact.resolve(entry["variant_id"]), self.digests)
            if candidates:
                checking.append(
                    {
                        "id": entry["slot_id"],
                        "display_name": entry["display_name"],
                        "files": candidates,
                        "message": f"Checking whether you already have {entry['display_name']}",
                    }
                )
        payload: Dict[str, Any] = {
            "kind": "modes",
            "preset_id": assessment.slot_plan.preset_id,
            "preset_name": assessment.slot_plan.preset_name,
            "target": {
                "backend_id": assessment.target.key,
                "label": assessment.target.label,
                "kind": assessment.target.kind,
                "options": [self._option_view(assessment, option) for option in assessment.options],
            },
            "gpu": assessment.gpu.to_dict(),
            "modes": modes_view,
            "slots": [self._slot_view(assessment, state) for state in visible_slots],
            "manifest": manifest,
            "disk": [disk.to_dict(assessment.target.key) for disk in assessment.profile.disks],
            "estimate": assessment.estimate,
            "artifacts": [
                {
                    "id": entry["slot_id"],
                    "variant_id": entry["variant_id"],
                    "display_name": entry["display_name"],
                    "size_bytes": entry["size_bytes"],
                    "kind": entry["model_type"],
                    "gated": entry["gated"],
                    "license_url": entry["license_url"],
                }
                for entry in default_files
            ],
            "total_bytes": sum(sizes) if all(size is not None for size in sizes) else None,
        }
        providers = credential_prompts(self._provider_registry, offered_missing)
        if providers:
            payload["providers"] = providers
        warnings = gated_warnings(self._provider_registry, offered_missing)
        if warnings:
            payload["warnings"] = warnings
        if checking:
            payload["checking"] = checking
        return payload

    def approve(
        self,
        assessment: PlanAssessment,
        modes: Optional[Sequence[str]] = None,
        extras: Optional[Sequence[str]] = None,
        selections: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        chosen_modes = list(dict.fromkeys(modes)) if modes is not None else self.default_modes(assessment)
        unknown = [m for m in chosen_modes if m not in assessment.slot_plan.modes]
        if unknown:
            raise PlanApprovalError(f"'{unknown[0]}' is not one of the modes this step offers.")
        missing_locked = [m for m in assessment.locked if m not in chosen_modes]
        if missing_locked:
            label = assessment.mode(missing_locked[0]).label
            raise PlanApprovalError(f"{label} is always included in this install.")
        chosen_extras = list(dict.fromkeys(extras)) if extras is not None else list(assessment.preselect_extras)
        allowed_extras = {
            sid for m in chosen_modes for sid in assessment.mode(m).extra_slot_ids
        } if assessment.extras_offered else set()
        for slot_id in chosen_extras:
            if slot_id not in allowed_extras:
                raise PlanApprovalError(f"'{slot_id}' is not an extra file of the modes you picked.")
        selections = {k: v for k, v in (selections or {}).items() if k in assessment.slots}
        for slot_id, variant_id in selections.items():
            if assessment.slots[slot_id].variant(variant_id) is None:
                raise PlanApprovalError(f"'{variant_id}' is not a file offered for '{slot_id}'.")
        files = self.selection_files(assessment, chosen_modes, chosen_extras, selections)
        problems = self.disk_shortfalls(assessment, files)
        if problems:
            raise PlanApprovalError(problems[0])
        chosen: Dict[str, Dict[str, Any]] = {}
        for slot_id in self.needed_slots(assessment, chosen_modes, chosen_extras):
            state = assessment.slots[slot_id]
            if state.manual or state.satisfied_by is not None:
                continue
            variant = self._chosen_variant(state, selections)
            if variant is None:
                continue
            chosen[slot_id] = {
                "fields": {m: f for m, f in state.slot.fields.items() if m in chosen_modes},
                "variant_id": variant["id"],
                "filename": variant["filename"],
                "model_type": state.slot.model_type,
                "installed": bool(variant.get("installed")),
            }
        sizes = [f["size_bytes"] for f in files]
        return {
            "preset_id": assessment.slot_plan.preset_id,
            "engine": assessment.slot_plan.engine,
            "target": assessment.target.key,
            "target_label": assessment.target.label,
            "destination_backend_id": assessment.profile.destination_backend_id,
            "modes": chosen_modes,
            "extras": chosen_extras,
            "files": files,
            "chosen": chosen,
            "bytes": sum(sizes) if all(size is not None for size in sizes) else None,
        }

    def summary(self, preset_id: str, selected: Sequence[str] = (), locked: Sequence[str] = ()) -> Optional[Dict[str, Any]]:
        key = (preset_id, tuple(selected), tuple(locked))
        cached = self._summaries.get(key)
        now = self._clock()
        if cached is not None and now - cached[0] < _SUMMARY_TTL_SECONDS:
            return cached[1]
        try:
            assessment = self.assess(
                preset_id, selected=selected, locked=locked, hash_candidates="never",
                probe_remote=False, with_estimate=False,
            )
        except PlanApprovalError:
            assessment = None
        if assessment is None:
            return None
        result = self._summarize(assessment)
        self._summaries[key] = (now, result)
        return result

    def _summarize(self, assessment: PlanAssessment) -> Dict[str, Any]:
        modes = []
        ready_count = 0
        for mode_id in assessment.offered:
            mode = assessment.mode(mode_id)
            ready = self.mode_ready(assessment, mode_id)
            ready_count += ready
            own = [sid for sid in mode.slot_ids if assessment.slot_plan.slots[sid].used_by == (mode_id,)]
            sizes = [self._slot_size(assessment.slots[sid]) for sid in mode.slot_ids]
            own_sizes = [self._slot_size(assessment.slots[sid]) for sid in own if assessment.slots[sid].installed]
            sharers = None
            for sid in mode.slot_ids:
                users = set(assessment.slot_plan.slots[sid].used_by) - {mode_id}
                sharers = users if sharers is None else sharers & users
            modes.append(
                {
                    "id": mode_id,
                    "label": mode.label,
                    "short_description": mode.short_description,
                    "ready": ready,
                    "size_bytes": sum(s for s in sizes if s) if sizes else 0,
                    "own_bytes": sum(s for s in own_sizes if s),
                    "missing_bytes": self.mode_missing_bytes(assessment, mode_id),
                    "removable": ready and bool(own_sizes),
                    "shares_with": [m for m in assessment.offered if m in (sharers or set())] if not own else [],
                }
            )
        installed_bytes = sum(
            self._slot_size(state) or 0 for state in assessment.slots.values() if state.installed
        )
        default_modes = self.default_modes(assessment)
        default_files = self.selection_files(assessment, default_modes, assessment.preselect_extras)
        total_needed = []
        for slot_id in self.needed_slots(assessment, default_modes, assessment.preselect_extras):
            state = assessment.slots[slot_id]
            if state.manual:
                continue
            chosen = self._chosen_variant(state, {})
            total_needed.append(chosen.get("size_bytes") if chosen else None)
        missing = [f["size_bytes"] for f in default_files]
        return {
            "preset_id": assessment.slot_plan.preset_id,
            "modes_total": len(assessment.offered),
            "modes_ready": ready_count,
            "installed_bytes": installed_bytes,
            "missing_bytes": sum(missing) if all(m is not None for m in missing) else None,
            "total_bytes": sum(total_needed) if all(t is not None for t in total_needed) else None,
            "modes": modes,
        }

    @staticmethod
    def _slot_size(state: SlotState) -> Optional[int]:
        if state.manual:
            return None
        for variant_id in state.installed:
            entry = state.variant(variant_id)
            if entry is not None:
                return entry.get("size_bytes")
        entry = state.variant(state.recommended_variant_id)
        return entry.get("size_bytes") if entry else None


def _restrict(artifact, variants):
    keep = list(variants)
    if not any(v.default for v in keep):
        keep[0] = replace(keep[0], default=True)
    return replace(artifact, variants=tuple(keep))
