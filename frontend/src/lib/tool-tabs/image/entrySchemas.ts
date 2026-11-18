import { get } from 'svelte/store';
import { effectDefaults } from '$lib/effects/catalog';
import type { EntryContext, RailToolEntry } from '$lib/editors/railTools';
import { filterCatalog } from '$lib/filters/catalog';
import { sharedExtensions } from '$lib/filters/extensions';
import { getOp } from '$lib/filters/engine';
import { scalarParams } from '$lib/filters/steps';
import { paramField, toolParamsToSchema } from '$lib/media-tools/toolSchema';
import type { ToolParam } from '$lib/media-tools/types';
import { paintFilters } from '$lib/paint/registries';
import { liveEdit, type EditStackState, type StepDraft } from '$lib/tool-tabs/frame/editStack';
import { adjustStepFor, setAdjustToggle, setAdjustValue, toggleIsOn } from './adjustSections';
import { GRADE_ENTRIES, LIVE_EFFECTS, OP_ENTRIES, controlKey, gradeValue, liveEffectState, matchEffect, setGradeValue, type GradeControl, type LiveEffectEntry } from './entries';
import { imageEffects } from './shaderEffect';
import { CURVE_POINTS, toolEffectDraft, type AdjustParams, type EffectParams, type ToolEffectParams } from './steps';
import type { ImageToolHost } from './toolHost';

type SchemaEntry = Pick<RailToolEntry, 'schema' | 'values' | 'onValues' | 'description'>;

const lookup = (op: string) => getOp(op, sharedExtensions(get(filterCatalog).ops, paintFilters.list()));

function imageHost(ctx: EntryContext): ImageToolHost {
	return ctx.host as ImageToolHost;
}

function stackOf(ctx: EntryContext): EditStackState<unknown> {
	return imageHost(ctx).current();
}

function effectOf(ctx: EntryContext, tool: string) {
	return imageEffects(ctx.catalog).find((candidate) => candidate.id === tool) ?? null;
}

function legacyValue(stack: EditStackState<unknown>, op: string, param: string, fallback: number): number {
	const found = adjustStepFor(stack, op);
	return found && !found.hidden ? ((found.params as AdjustParams).values[param] ?? fallback) : fallback;
}

function legacyParam(entry: LiveEffectEntry, opParam: string) {
	return scalarParams(lookup(entry.legacyOp)).find((candidate) => candidate.id === opParam);
}

function liveEffectSchema(entry: LiveEffectEntry, ctx: EntryContext) {
	const fx = effectOf(ctx, entry.id);
	const state = liveEffectState(ctx.stack ?? stackOf(ctx), entry);
	if (state.shape !== 'legacy') return fx ? toolParamsToSchema(fx.params) : [];
	const fields = [];
	for (const [name, opParam] of Object.entries(entry.legacy)) {
		const param = legacyParam(entry, opParam);
		if (!param) continue;
		const label = fx?.params.find((candidate) => candidate.name === name)?.label ?? param.label ?? name;
		fields.push(
			paramField({
				name,
				label,
				type: param.type === 'float' ? 'float' : 'integer',
				min: param.min,
				max: param.max,
				step: param.type === 'float' ? 0.05 : 1,
				default: param.default
			})
		);
	}
	return fields;
}

function liveEffectValues(entry: LiveEffectEntry, ctx: EntryContext): Record<string, unknown> {
	const stack = ctx.stack ?? stackOf(ctx);
	const fx = effectOf(ctx, entry.id);
	const state = liveEffectState(stack, entry);
	if (state.shape === 'legacy') {
		const values: Record<string, unknown> = {};
		for (const [name, opParam] of Object.entries(entry.legacy)) {
			const param = legacyParam(entry, opParam);
			if (param) values[name] = legacyValue(stack, entry.legacyOp, opParam, param.default);
		}
		return values;
	}
	if (state.shape === 'effect') return { ...(state.step!.params as ToolEffectParams).values };
	return fx ? effectDefaults(fx) : {};
}

function writeLiveEffect(entry: LiveEffectEntry, values: Record<string, unknown>, ctx: EntryContext): void {
	const host = imageHost(ctx);
	const stack = host.current();
	const state = liveEffectState(stack, entry);
	if (state.shape === 'legacy') {
		const spec = lookup(entry.legacyOp);
		if (!spec) return;
		let next = stack;
		for (const [name, opParam] of Object.entries(entry.legacy)) {
			const param = legacyParam(entry, opParam);
			if (!param || typeof values[name] !== 'number') continue;
			if (legacyValue(next, entry.legacyOp, opParam, param.default) === values[name]) continue;
			next = setAdjustValue(next, spec, opParam, values[name] as number);
		}
		if (next !== stack) host.edit(next, { hold: true });
		return;
	}
	const fx = effectOf(ctx, entry.id);
	if (!fx) return;
	const params: ToolEffectParams = { kind: 'effect', tool: entry.id, name: fx.label, values };
	host.edit(liveEdit(stack, toolEffectDraft(params, ctx.catalog) as StepDraft<unknown>, matchEffect(entry.id)), { hold: true });
}

export function liveEffectEntry(id: LiveEffectEntry['id']): SchemaEntry {
	const entry = LIVE_EFFECTS[id];
	return {
		schema: (ctx) => liveEffectSchema(entry, ctx),
		description: (ctx) => effectOf(ctx, entry.id)?.description ?? '',
		values: (ctx) => liveEffectValues(entry, ctx),
		onValues: (values, info, ctx) => {
			if (info.commit) imageHost(ctx).settle();
			else writeLiveEffect(entry, values, ctx);
		}
	};
}

export function toggleEntry(id: 'grayscale' | 'invert'): SchemaEntry {
	const op = OP_ENTRIES[id].op;
	return {
		schema: () => [
			{ type: 'checkbox', name: 'on', title: lookup(op)?.label ?? id, default: false } as never
		],
		values: (ctx) => ({ on: toggleIsOn(ctx.stack ?? stackOf(ctx), op) }),
		onValues: (values, info, ctx) => {
			const host = imageHost(ctx);
			if (info.commit) {
				host.settle();
				return;
			}
			const spec = lookup(op);
			if (spec) host.edit(setAdjustToggle(host.current(), spec, values.on === true), { hold: true });
		}
	};
}

function heldEffect(ctx: EntryContext): ToolEffectParams | null {
	const params = ctx.pending?.params as EffectParams | undefined;
	return params?.kind === 'effect' && params.tool === ctx.tool ? params : null;
}

export function effectCardEntry(): SchemaEntry {
	return {
		schema: (ctx) => {
			const fx = effectOf(ctx, ctx.tool);
			return fx ? toolParamsToSchema(fx.params) : [];
		},
		description: (ctx) => {
			const fx = effectOf(ctx, ctx.tool);
			return fx ? fx.description : 'This effect is not available right now.';
		},
		values: (ctx) => {
			const held = heldEffect(ctx);
			if (held) return held.values;
			const fx = effectOf(ctx, ctx.tool);
			return fx ? effectDefaults(fx) : {};
		},
		onValues: (values, info, ctx) => {
			if (info.commit) return;
			const fx = effectOf(ctx, ctx.tool);
			if (!fx) return;
			const params: ToolEffectParams = { kind: 'effect', tool: ctx.tool, name: fx.label, values };
			const draft = toolEffectDraft(params, ctx.catalog) as StepDraft<unknown>;
			if (heldEffect(ctx)) ctx.host.update(draft);
			else ctx.host.begin(draft);
		}
	};
}

const SIGNED_SLIDERS = { signedSliders: true } as const;

export const gradeFieldName = (control: GradeControl): string => controlKey(control).replace('.', '_');

function opSlider(name: string, label: string, param: { type: string; min: number; max: number; default: number; unit?: string | null }) {
	const tool: ToolParam = {
		name,
		label,
		type: param.type === 'float' ? 'float' : 'integer',
		min: param.min,
		max: param.max,
		step: param.type === 'float' ? 0.05 : 1,
		default: param.default,
		unit: param.unit ?? undefined
	};
	return paramField(tool, SIGNED_SLIDERS);
}

export function gradeEntry(id: 'light' | 'colour'): SchemaEntry {
	const entry = GRADE_ENTRIES[id];
	return {
		schema: () =>
			entry.controls.flatMap((control) => {
				const param = scalarParams(lookup(control.op)).find((candidate) => candidate.id === control.param);
				return param ? [opSlider(gradeFieldName(control), control.label, param)] : [];
			}),
		values: (ctx) => {
			const stack = ctx.stack ?? stackOf(ctx);
			return Object.fromEntries(entry.controls.map((control) => [gradeFieldName(control), gradeValue(stack, entry, control, lookup)]));
		},
		onValues: (values, info, ctx) => {
			const host = imageHost(ctx);
			if (info.commit) {
				host.settle();
				return;
			}
			const before = host.current();
			let next = before;
			for (const control of entry.controls) {
				const value = values[gradeFieldName(control)];
				if (typeof value !== 'number' || gradeValue(next, entry, control, lookup) === value) continue;
				next = setGradeValue(next, entry, control, value, lookup);
			}
			if (next !== before) host.edit(next, { hold: true });
		}
	};
}

export function opSlidersEntry(id: 'split_tone' | 'curves'): SchemaEntry {
	const entry = OP_ENTRIES[id];
	const controls: ReadonlyArray<{ param: string; label: string }> = id === 'curves' ? CURVE_POINTS.map((point) => ({ param: point.id, label: point.label })) : (entry.controls ?? []);
	const paramOf = (param: string) => (id === 'curves' ? { type: 'integer', min: -100, max: 100, default: 0 } : scalarParams(lookup(entry.op)).find((candidate) => candidate.id === param));
	return {
		schema: () =>
			controls.flatMap((control) => {
				const param = paramOf(control.param);
				return param ? [opSlider(control.param, control.label, param)] : [];
			}),
		values: (ctx) => {
			const stack = ctx.stack ?? stackOf(ctx);
			return Object.fromEntries(controls.map((control) => [control.param, legacyValue(stack, entry.op, control.param, paramOf(control.param)?.default ?? 0)]));
		},
		onValues: (values, info, ctx) => {
			const host = imageHost(ctx);
			if (info.commit) {
				host.settle();
				return;
			}
			const spec = lookup(entry.op);
			if (!spec) return;
			const before = host.current();
			let next = before;
			for (const control of controls) {
				const value = values[control.param];
				if (typeof value !== 'number' || legacyValue(next, entry.op, control.param, paramOf(control.param)?.default ?? 0) === value) continue;
				next = setAdjustValue(next, spec, control.param, value);
			}
			if (next !== before) host.edit(next, { hold: true });
		}
	};
}
