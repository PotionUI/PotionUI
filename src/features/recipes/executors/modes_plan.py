from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.features.recipes.executors.base import GrantRequest, LintContext, StepContext, StepResult
from src.features.recipes.mode_slots import PlanApprovalError, derive_mode_slots
from src.features.recipes.records import MODE_ADMIN

if TYPE_CHECKING:
    from src.features.recipes.mode_plans import ModePlanner, PlanAssessment

PRESELECT_KEY = "preselect_modes"


def _id_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str) and v]
    return []


def preselected_modes(context: StepContext) -> List[str]:
    return _id_list((context.run.safe_input or {}).get(PRESELECT_KEY))


class ModesPlanExecutor:
    def __init__(self, planner: "ModePlanner"):
        self.planner = planner

    def _assess(self, context: StepContext, target: Optional[str] = None, **kwargs) -> "PlanAssessment":
        params = context.step.params
        preset_id = params.get("preset_id")
        assessment = self.planner.assess(
            preset_id,
            modes=params.get("modes", "all"),
            selected=_id_list(params.get("selected")),
            locked=_id_list(params.get("locked")),
            preselect=preselected_modes(context),
            extras=params.get("extras", "offer"),
            preselect_extras=_id_list(params.get("preselect_extras")),
            target=target if target is not None else params.get("target"),
            **kwargs,
        )
        if assessment is None:
            raise PlanApprovalError(f"The preset this step plans for ('{preset_id}') isn't available on this installation.")
        return assessment

    def execute(self, context: StepContext) -> StepResult:
        if not context.step.params.get("preset_id"):
            return StepResult.fail("MODES_PLAN_MISCONFIGURED", "This step doesn't say which preset to plan for.")
        try:
            assessment = self._assess(context)
        except PlanApprovalError as exc:
            return StepResult.fail("MODES_PLAN_UNAVAILABLE", str(exc))
        defaults = self.planner.default_modes(assessment)
        files = self.planner.selection_files(assessment, defaults, assessment.preselect_extras)
        unready = [m for m in assessment.offered if not self.planner.mode_ready(assessment, m)]
        if not files and not (context.run.mode == MODE_ADMIN and unready):
            try:
                plan = self.planner.approve(assessment)
            except PlanApprovalError as exc:
                return StepResult.fail("MODES_PLAN_UNAVAILABLE", str(exc))
            return StepResult.ok(
                {
                    "plan": plan,
                    "message": "Every file the chosen modes need is already here - nothing to download.",
                }
            )
        return StepResult.awaiting(self.planner.consent_payload(assessment))

    def grant(self, context: StepContext, consent_request: Dict[str, Any], grant: GrantRequest) -> Dict[str, Any]:
        target = grant.target or ((consent_request or {}).get("target") or {}).get("backend_id")
        assessment = self._assess(context, target=target)
        modes = grant.modes
        if modes is None:
            modes = [m["id"] for m in (consent_request or {}).get("modes") or [] if m.get("selected")] or None
        plan = self.planner.approve(assessment, modes, grant.extras, grant.selections)
        return {"plan": plan}

    def preview(self, context: StepContext, target: Optional[str] = None) -> Dict[str, Any]:
        assessment = self._assess(context, target=target, probe_remote=True)
        if not preselected_modes(context):
            unready = [m for m in assessment.offered if not self.planner.mode_ready(assessment, m)]
            if unready:
                assessment = replace(assessment, selected=list(dict.fromkeys([*assessment.selected, *unready])))
        payload = self.planner.consent_payload(assessment)
        ready = sum(1 for m in payload["modes"] if m["ready"])
        return {
            "summary": f"{len(payload['modes'])} modes, {ready} ready",
            "consent_request": payload,
        }

    def validate_params(self, params: Dict[str, Any], recipe: Any, lint: LintContext) -> List[str]:
        issues: List[str] = []
        preset_id = params.get("preset_id")
        preset = (lint.preset_index or {}).get(preset_id)
        if preset is None:
            return [f"modes.plan: preset '{preset_id}' does not exist"]
        modes = params.get("modes", "all")
        listed = list(preset.modes) if modes == "all" else _id_list(modes)
        for key, values in (("modes", [] if modes == "all" else listed), ("selected", _id_list(params.get("selected"))), ("locked", _id_list(params.get("locked")))):
            for mode in values:
                if mode not in (preset.modes or {}):
                    issues.append(f"modes.plan: params.{key} names '{mode}', which is not a mode of '{preset.name}'")
        plan = derive_mode_slots(preset, [m for m in listed if m in (preset.modes or {})])
        offered_extras = {sid for mode in plan.modes.values() for sid in mode.extra_slot_ids}
        for slot_id in _id_list(params.get("preselect_extras")):
            if slot_id not in offered_extras:
                issues.append(f"modes.plan: params.preselect_extras names '{slot_id}', which is not an extra file of these modes")
        return issues
