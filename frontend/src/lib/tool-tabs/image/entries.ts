import type { OpSpec } from '$lib/filters/engine';
import { scalarParams } from '$lib/filters/steps';
import { stepKinds, reopenToolOf } from '$lib/editors/stepKinds';
import { appliedSteps, commitLive, liveEdit, removeUndoable, type EditStackState, type EditStep, type StepDraft } from '$lib/tool-tabs/frame/editStack';
import { adjustStepFor, isAdjustParams, matchAdjust, resetAdjustOp, sameValues, setAdjustToggle, setAdjustValue } from './adjustSections';
import { type AdjustParams, type EffectParams, type ToolEffectParams } from './steps';

export type Lookup = (op: string) => OpSpec | undefined;

export interface GradeControl {
	op: string;
	param: string;
	label: string;
}

export interface GradeEntry {
	id: 'light' | 'colour';
	label: string;
	icon: string;
	bundle: string;
	controls: readonly GradeControl[];
}

export const GRADE_ENTRIES: Readonly<Record<'light' | 'colour', GradeEntry>> = {
	light: {
		id: 'light',
		label: 'Light',
		icon: 'lightbulb',
		bundle: 'light',
		controls: [
			{ op: 'exposure', param: 'stops', label: 'Exposure' },
			{ op: 'tone', param: 'brightness', label: 'Brightness' },
			{ op: 'tone', param: 'contrast', label: 'Contrast' },
			{ op: 'fade', param: 'black_lift', label: 'Black lift' },
			{ op: 'fade', param: 'white_cap', label: 'White cap' }
		]
	},
	colour: {
		id: 'colour',
		label: 'Colour',
		icon: 'palette',
		bundle: 'colour',
		controls: [
			{ op: 'white_balance', param: 'temperature', label: 'Temperature' },
			{ op: 'white_balance', param: 'tint', label: 'Tint' },
			{ op: 'tone', param: 'saturation', label: 'Saturation' },
			{ op: 'tone', param: 'hue', label: 'Hue' },
			{ op: 'vibrance', param: 'amount', label: 'Vibrance' }
		]
	}
};

export interface OpEntry {
	id: 'split_tone' | 'curves' | 'grayscale' | 'invert';
	op: string;
	kind: 'sliders' | 'curves' | 'toggle';
	controls?: readonly { param: string; label: string }[];
}

export const OP_ENTRIES: Readonly<Record<OpEntry['id'], OpEntry>> = {
	split_tone: {
		id: 'split_tone',
		op: 'split_tone',
		kind: 'sliders',
		controls: [
			{ param: 'shadow_hue', label: 'Shadow hue' },
			{ param: 'shadow_sat', label: 'Shadow saturation' },
			{ param: 'highlight_hue', label: 'Highlight hue' },
			{ param: 'highlight_sat', label: 'Highlight saturation' },
			{ param: 'balance', label: 'Balance' }
		]
	},
	curves: { id: 'curves', op: 'curves', kind: 'curves' },
	grayscale: { id: 'grayscale', op: 'grayscale', kind: 'toggle' },
	invert: { id: 'invert', op: 'invert', kind: 'toggle' }
};

export interface LiveEffectEntry {
	id: 'film_grain' | 'vignette';
	legacyOp: string;
	legacy: Readonly<Record<string, string>>;
}

export const LIVE_EFFECTS: Readonly<Record<LiveEffectEntry['id'], LiveEffectEntry>> = {
	film_grain: { id: 'film_grain', legacyOp: 'grain', legacy: { amount: 'amount', size: 'size', seed: 'seed' } },
	vignette: { id: 'vignette', legacyOp: 'vignette', legacy: { amount: 'amount', radius: 'midpoint', softness: 'feather' } }
};

export const OP_TO_ENTRY: Readonly<Record<string, string>> = {
	exposure: 'light',
	tone: 'light',
	fade: 'light',
	white_balance: 'colour',
	vibrance: 'colour',
	split_tone: 'split_tone',
	curves: 'curves',
	grayscale: 'grayscale',
	invert: 'invert',
	grain: 'film_grain',
	vignette: 'vignette',
	light: 'light',
	colour: 'colour'
};

export function isGradeEntry(id: string | null | undefined): id is 'light' | 'colour' {
	return id === 'light' || id === 'colour';
}

export function isOpEntry(id: string | null | undefined): id is OpEntry['id'] {
	return !!id && id in OP_ENTRIES;
}

export function isLiveEffectEntry(id: string | null | undefined): id is LiveEffectEntry['id'] {
	return !!id && id in LIVE_EFFECTS;
}

export function isLiveEntry(id: string | null | undefined): boolean {
	return isGradeEntry(id) || isOpEntry(id) || isLiveEffectEntry(id);
}

export function controlKey(control: GradeControl): string {
	return `${control.op}.${control.param}`;
}

function paramDefault(lookup: Lookup, op: string, param: string): number {
	return scalarParams(lookup(op)).find((entry) => entry.id === param)?.default ?? 0;
}

export function gradeDefaults(entry: GradeEntry, lookup: Lookup): Record<string, number> {
	return Object.fromEntries(entry.controls.map((control) => [controlKey(control), paramDefault(lookup, control.op, control.param)]));
}

export function bundleStep(stack: EditStackState<unknown>, bundle: string): EditStep | undefined {
	return appliedSteps(stack).findLast(matchAdjust(bundle));
}

function bundleValues(stack: EditStackState<unknown>, entry: GradeEntry, lookup: Lookup): Record<string, number> {
	const defaults = gradeDefaults(entry, lookup);
	const step = bundleStep(stack, entry.bundle);
	if (!step || step.hidden) return defaults;
	return { ...defaults, ...(step.params as AdjustParams).values };
}

export function gradeValue(stack: EditStackState<unknown>, entry: GradeEntry, control: GradeControl, lookup: Lookup): number {
	const legacy = adjustStepFor(stack, control.op);
	const fallback = paramDefault(lookup, control.op, control.param);
	if (legacy && !legacy.hidden) return (legacy.params as AdjustParams).values[control.param] ?? fallback;
	return bundleValues(stack, entry, lookup)[controlKey(control)] ?? fallback;
}

function gradeSummary(entry: GradeEntry, values: Record<string, number>, defaults: Record<string, number>): string {
	const parts = entry.controls
		.filter((control) => values[controlKey(control)] !== defaults[controlKey(control)])
		.slice(0, 3)
		.map((control) => {
			const value = values[controlKey(control)];
			const shown = Number.isInteger(value) ? String(value) : String(Math.round(value * 100) / 100);
			return `${control.label} ${value > 0 ? '+' : ''}${shown}`;
		});
	return parts.join(' · ') || 'On';
}

function bundleDraft(entry: GradeEntry, values: Record<string, number>, defaults: Record<string, number>): StepDraft<AdjustParams> {
	const params: AdjustParams = { kind: 'adjust', op: entry.bundle, name: entry.label, values };
	return { tool: 'effects', icon: entry.icon, label: entry.label, summary: gradeSummary(entry, values, defaults), params };
}

export function setGradeValue(stack: EditStackState<unknown>, entry: GradeEntry, control: GradeControl, value: number, lookup: Lookup): EditStackState<unknown> {
	const legacy = adjustStepFor(stack, control.op);
	if (legacy) {
		const spec = lookup(control.op);
		return spec ? setAdjustValue(stack, spec, control.param, value) : stack;
	}
	const defaults = gradeDefaults(entry, lookup);
	const next = { ...bundleValues(stack, entry, lookup), [controlKey(control)]: value };
	return liveEdit(stack, bundleDraft(entry, next, defaults) as StepDraft<unknown>, matchAdjust(entry.bundle), { identity: sameValues(next, defaults) });
}

export function resetGrade(stack: EditStackState<unknown>, entry: GradeEntry, lookup: Lookup): EditStackState<unknown> {
	let next = commitLive(stack);
	for (const control of entry.controls) {
		const legacy = adjustStepFor(next, control.op);
		if (!legacy) continue;
		const fallback = paramDefault(lookup, control.op, control.param);
		if (gradeValue(next, entry, control, lookup) !== fallback) next = setGradeValue(next, entry, control, fallback, lookup);
	}
	next = commitLive(next);
	const step = bundleStep(next, entry.bundle);
	if (step) {
		const defaults = gradeDefaults(entry, lookup);
		next = commitLive(liveEdit(next, bundleDraft(entry, defaults, defaults) as StepDraft<unknown>, matchAdjust(entry.bundle), { identity: true }));
	}
	return next;
}

export function gradeApplied(stack: EditStackState<unknown>, entry: GradeEntry, lookup: Lookup): boolean {
	return entry.controls.some((control) => gradeValue(stack, entry, control, lookup) !== paramDefault(lookup, control.op, control.param));
}

export function opApplied(stack: EditStackState<unknown>, op: string, lookup: Lookup): boolean {
	const step = adjustStepFor(stack, op);
	if (!step || step.hidden) return false;
	const spec = lookup(op);
	if (!spec || spec.params.length === 0) return true;
	const defaults: Record<string, number> = {};
	for (const param of scalarParams(spec)) defaults[param.id] = param.default;
	const values = (step.params as AdjustParams).values;
	if (op === 'curves') return Object.values(values).some((value) => value !== 0);
	return !sameValues({ ...defaults, ...values }, defaults);
}

export function effectStep(stack: EditStackState<unknown>, tool: string): EditStep | undefined {
	return appliedSteps(stack).findLast((step) => step.tool === 'effects' && (step.params as EffectParams | null)?.kind === 'effect' && (step.params as ToolEffectParams).tool === tool);
}

export interface LiveEffectState {
	shape: 'effect' | 'legacy' | 'none';
	step: EditStep | undefined;
}

export function liveEffectState(stack: EditStackState<unknown>, entry: LiveEffectEntry): LiveEffectState {
	const effect = effectStep(stack, entry.id);
	const legacy = adjustStepFor(stack, entry.legacyOp);
	if (effect && legacy) return stack.steps.indexOf(effect) > stack.steps.indexOf(legacy) ? { shape: 'effect', step: effect } : { shape: 'legacy', step: legacy };
	if (effect) return { shape: 'effect', step: effect };
	if (legacy) return { shape: 'legacy', step: legacy };
	return { shape: 'none', step: undefined };
}

export function entryOfStep(step: Pick<EditStep, 'tool' | 'params'>): string | null {
	if (step.tool === 'effects') {
		const params = step.params as EffectParams | null;
		if (!params) return 'effects';
		if (isAdjustParams(params)) return OP_TO_ENTRY[params.op] ?? 'light';
		if (params.kind === 'effect') return params.tool;
		return 'effects';
	}
	const kind = stepKinds.get('image', step.tool);
	return (kind && reopenToolOf(kind, step.params)) ?? step.tool;
}

export function appliedEntries(stack: EditStackState<unknown>, lookup: Lookup): Set<string> {
	const out = new Set<string>();
	for (const step of appliedSteps(stack)) {
		if (step.hidden) continue;
		const entry = entryOfStep(step);
		if (entry && !isLiveEntry(entry)) out.add(entry);
	}
	for (const entry of Object.values(GRADE_ENTRIES)) if (gradeApplied(stack, entry, lookup)) out.add(entry.id);
	for (const entry of Object.values(OP_ENTRIES)) if (opApplied(stack, entry.op, lookup)) out.add(entry.id);
	for (const entry of Object.values(LIVE_EFFECTS)) {
		const state = liveEffectState(stack, entry);
		if (state.step && !state.step.hidden) out.add(entry.id);
	}
	return out;
}

export function matchEffect(tool: string): (step: EditStep) => boolean {
	return (step) => step.tool === 'effects' && (step.params as EffectParams | null)?.kind === 'effect' && (step.params as ToolEffectParams).tool === tool;
}

export function resetEntry(stack: EditStackState<unknown>, id: string, lookup: Lookup): EditStackState<unknown> {
	const settled = commitLive(stack);
	if (isGradeEntry(id)) return resetGrade(settled, GRADE_ENTRIES[id], lookup);
	if (isOpEntry(id)) {
		const entry = OP_ENTRIES[id];
		const spec = lookup(entry.op);
		if (!spec) return settled;
		if (entry.kind === 'toggle') return setAdjustToggle(settled, spec, false);
		return resetAdjustOp(settled, spec);
	}
	if (isLiveEffectEntry(id)) {
		const entry = LIVE_EFFECTS[id];
		let next = settled;
		const effect = effectStep(next, id);
		if (effect) next = removeUndoable(next, effect.id);
		const legacy = adjustStepFor(next, entry.legacyOp);
		const spec = lookup(entry.legacyOp);
		if (legacy && spec) next = resetAdjustOp(next, spec);
		return next;
	}
	return settled;
}
