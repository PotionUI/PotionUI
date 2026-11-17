import type { OpSpec } from '$lib/filters/engine';
import { scalarParams } from '$lib/filters/steps';
import { appliedSteps, commitLive, liveEdit, toggleHidden, type EditStackState, type EditStep, type StepDraft } from '$lib/tool-tabs/frame/editStack';
import { CURVE_POINTS, adjustDraft, type AdjustParams } from './steps';

export const ADJUST_STEP_TOOL = 'effects';

export function isAdjustParams(params: unknown): params is AdjustParams {
	return (params as AdjustParams | null)?.kind === 'adjust';
}

export function matchAdjust(op: string): (step: EditStep) => boolean {
	return (step) => step.tool === ADJUST_STEP_TOOL && isAdjustParams(step.params) && (step.params as AdjustParams).op === op;
}

export function opDefaults(op: string, spec: OpSpec | undefined): Record<string, number> {
	const values: Record<string, number> = {};
	if (!spec) return values;
	if (op === 'curves') for (const point of CURVE_POINTS) values[point.id] = 0;
	for (const param of scalarParams(spec)) values[param.id] = param.default;
	return values;
}

export function adjustStepFor(stack: EditStackState<unknown>, op: string): EditStep | undefined {
	return appliedSteps(stack).findLast(matchAdjust(op));
}

export function adjustValues(stack: EditStackState<unknown>, op: string, defaults: Record<string, number>): Record<string, number> {
	const step = adjustStepFor(stack, op);
	if (!step || step.hidden) return { ...defaults };
	return { ...defaults, ...(step.params as AdjustParams).values };
}

export function sameValues(a: Record<string, number>, b: Record<string, number>): boolean {
	const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
	for (const key of keys) if ((a[key] ?? 0) !== (b[key] ?? 0)) return false;
	return true;
}

export function adjustSummaryOf(values: Record<string, number>, defaults: Record<string, number>, labels: Record<string, string>): string {
	const parts = Object.entries(values)
		.filter(([key, value]) => value !== (defaults[key] ?? 0))
		.slice(0, 3)
		.map(([key, value]) => `${labels[key] ?? key} ${Number.isInteger(value) ? value : Math.round(value * 100) / 100}`);
	return parts.join(' · ') || 'On';
}

export function liveAdjustDraft(spec: OpSpec, values: Record<string, number>): StepDraft<AdjustParams> {
	const defaults = opDefaults(spec.id, spec);
	const labels: Record<string, string> = Object.fromEntries(scalarParams(spec).map((param) => [param.id, param.label]));
	if (spec.id === 'curves') for (const point of CURVE_POINTS) labels[point.id] = point.label;
	const params: AdjustParams = { kind: 'adjust', op: spec.id, name: spec.label, values };
	return { ...adjustDraft(params), summary: adjustSummaryOf(values, defaults, labels) };
}

export function setAdjustValue(stack: EditStackState<unknown>, spec: OpSpec, param: string, value: number): EditStackState<unknown> {
	const defaults = opDefaults(spec.id, spec);
	const next = { ...adjustValues(stack, spec.id, defaults), [param]: value };
	return liveEdit(stack, liveAdjustDraft(spec, next) as StepDraft<unknown>, matchAdjust(spec.id), { identity: sameValues(next, defaults) });
}

export function resetAdjustOp(stack: EditStackState<unknown>, spec: OpSpec): EditStackState<unknown> {
	const step = adjustStepFor(stack, spec.id);
	if (!step) return stack;
	const defaults = opDefaults(spec.id, spec);
	return commitLive(liveEdit(stack, liveAdjustDraft(spec, defaults) as StepDraft<unknown>, matchAdjust(spec.id), { identity: true }));
}

export function setAdjustToggle(stack: EditStackState<unknown>, spec: OpSpec, on: boolean): EditStackState<unknown> {
	const step = adjustStepFor(stack, spec.id);
	if (on) return liveEdit(stack, liveAdjustDraft(spec, {}) as StepDraft<unknown>, matchAdjust(spec.id));
	if (!step || step.hidden) return stack;
	return toggleHidden(commitLive(stack), step.id);
}

export function toggleIsOn(stack: EditStackState<unknown>, op: string): boolean {
	const step = adjustStepFor(stack, op);
	return !!step && !step.hidden;
}

