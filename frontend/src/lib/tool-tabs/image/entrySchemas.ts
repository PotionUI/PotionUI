import { get } from 'svelte/store';
import { effectDefaults } from '$lib/effects/catalog';
import type { EntryContext, RailToolEntry } from '$lib/editors/railTools';
import { filterCatalog } from '$lib/filters/catalog';
import { sharedExtensions } from '$lib/filters/extensions';
import { getOp } from '$lib/filters/engine';
import { scalarParams } from '$lib/filters/steps';
import { paramField, toolParamsToSchema } from '$lib/media-tools/toolSchema';
import { paintFilters } from '$lib/paint/registries';
import { liveEdit, type EditStackState, type StepDraft } from '$lib/tool-tabs/frame/editStack';
import { adjustStepFor, setAdjustToggle, setAdjustValue, toggleIsOn } from './adjustSections';
import { LIVE_EFFECTS, OP_ENTRIES, liveEffectState, matchEffect, type LiveEffectEntry } from './entries';
import { imageEffects } from './shaderEffect';
import { toolEffectDraft, type AdjustParams, type EffectParams, type ToolEffectParams } from './steps';
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
