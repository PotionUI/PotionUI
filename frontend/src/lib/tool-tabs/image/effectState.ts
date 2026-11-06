import { effectDefaults, sameValues, type EffectTool } from '$lib/effects/catalog';
import type { FilterItem } from '$lib/filters/types';
import type { EffectParams, ToolEffectParams } from './steps';

export function comparableEffect(value: EffectParams): Record<string, unknown> {
	if (value.kind === 'look') return { filter: value.filter, intensity: value.intensity };
	if (value.kind === 'adjust') return { op: value.op, ...value.values };
	return { tool: value.tool, ...value.values };
}

export function effectDefaultsOf(value: EffectParams, items: readonly FilterItem[], effects: readonly EffectTool[]): EffectParams {
	if (value.kind === 'look') {
		const item = items.find((entry) => entry.id === value.filter);
		return { ...value, intensity: item?.intensity ?? value.intensity };
	}
	if (value.kind === 'adjust') return value;
	const effect = effects.find((entry) => entry.id === value.tool);
	return { ...value, values: effect ? effectDefaults(effect) : value.values } as ToolEffectParams;
}

export function canResetEffect(value: EffectParams | null, items: readonly FilterItem[], effects: readonly EffectTool[]): boolean {
	if (!value) return false;
	return !sameValues(comparableEffect(value), comparableEffect(effectDefaultsOf(value, items, effects)));
}

export function canApplyEffect(value: EffectParams | null, original: EffectParams | null, updating: boolean): boolean {
	if (!value) return false;
	return !updating || !original || !sameValues(comparableEffect(value), comparableEffect(original));
}
