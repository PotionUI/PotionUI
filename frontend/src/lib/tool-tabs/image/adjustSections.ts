import type { OpSpec } from '$lib/filters/engine';
import { scalarParams } from '$lib/filters/steps';
import { appliedSteps, commitLive, liveEdit, toggleHidden, type EditStackState, type EditStep, type StepDraft } from '$lib/tool-tabs/frame/editStack';
import { CURVE_POINTS, adjustDraft, type AdjustParams } from './steps';

export type AdjustControl =
	| { kind: 'slider'; op: string; param: string }
	| { kind: 'curves'; op: 'curves' }
	| { kind: 'toggle'; op: string };

export interface AdjustSection {
	id: 'light' | 'colour' | 'effects';
	label: string;
	icon: string;
	controls: AdjustControl[];
}

const slider = (op: string, param: string): AdjustControl => ({ kind: 'slider', op, param });

export const ADJUST_SECTIONS: readonly AdjustSection[] = [
	{
		id: 'light',
		label: 'Light',
		icon: 'lightbulb',
		controls: [slider('exposure', 'stops'), slider('tone', 'brightness'), slider('tone', 'contrast'), slider('fade', 'black_lift'), slider('fade', 'white_cap'), { kind: 'curves', op: 'curves' }]
	},
	{
		id: 'colour',
		label: 'Colour',
		icon: 'palette',
		controls: [
			slider('white_balance', 'temperature'),
			slider('white_balance', 'tint'),
			slider('tone', 'saturation'),
			slider('tone', 'hue'),
			slider('vibrance', 'amount'),
			slider('split_tone', 'shadow_hue'),
			slider('split_tone', 'shadow_sat'),
			slider('split_tone', 'highlight_hue'),
			slider('split_tone', 'highlight_sat'),
			slider('split_tone', 'balance')
		]
	},
	{
		id: 'effects',
		label: 'Effects',
		icon: 'sparkles',
		controls: [
			slider('vignette', 'amount'),
			slider('vignette', 'midpoint'),
			slider('vignette', 'feather'),
			slider('grain', 'amount'),
			slider('grain', 'size'),
			slider('grain', 'seed'),
			{ kind: 'toggle', op: 'grayscale' },
			{ kind: 'toggle', op: 'invert' }
		]
	}
];

export const ADJUST_STEP_TOOL = 'effects';

export function isAdjustParams(params: unknown): params is AdjustParams {
	return (params as AdjustParams | null)?.kind === 'adjust';
}

export function isAdjustStep(step: Pick<EditStep, 'tool' | 'params'>): boolean {
	return step.tool === ADJUST_STEP_TOOL && isAdjustParams(step.params);
}

export function matchAdjust(op: string): (step: EditStep) => boolean {
	return (step) => isAdjustStep(step) && (step.params as AdjustParams).op === op;
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

export function controlOps(section: AdjustSection): string[] {
	return [...new Set(section.controls.map((control) => control.op))];
}

export function sectionEdited(section: AdjustSection, stack: EditStackState<unknown>, lookup: (op: string) => OpSpec | undefined): boolean {
	return controlOps(section).some((op) => {
		const step = adjustStepFor(stack, op);
		if (!step || step.hidden) return false;
		const defaults = opDefaults(op, lookup(op));
		return !sameValues({ ...defaults, ...(step.params as AdjustParams).values }, defaults) || lookup(op)?.params.length === 0;
	});
}

export function adjustEditedCount(stack: EditStackState<unknown>): number {
	return appliedSteps(stack).filter((step) => isAdjustStep(step) && !step.hidden).length;
}
