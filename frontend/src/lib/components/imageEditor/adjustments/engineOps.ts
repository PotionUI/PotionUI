import { OP_CATALOGUE, type OpDef, type OpParam } from '$lib/filters/engine';
import { renderRecipe } from '$lib/filters/render';
import type { FilterValues, PaintFilter } from '../types';

const ADJUSTMENT_OPS = [
	'exposure',
	'white_balance',
	'curves',
	'vibrance',
	'split_tone',
	'fade',
	'vignette',
	'grain'
] as const;

function unitFor(param: OpParam): string | undefined {
	return param.id.endsWith('_hue') ? '°' : undefined;
}

function stepFor(param: OpParam): number | undefined {
	return param.type === 'float' ? 0.1 : undefined;
}

function defaultsOf(def: OpDef): Record<string, number> {
	const defaults: Record<string, number> = {};
	for (const param of def.params) if (param.type !== 'points') defaults[param.id] = param.default;
	return defaults;
}

export function adjustmentFromOp(def: OpDef): PaintFilter {
	const scalar = def.params.filter((param) => param.type !== 'points');
	const defaults = defaultsOf(def);
	const plot = def.params.some((param) => param.type === 'points');
	return {
		id: def.id,
		label: def.label,
		plot,
		params: scalar.map((param) => ({
			id: param.id,
			label: param.label,
			min: param.min,
			max: param.max,
			value: param.default,
			unit: unitFor(param),
			step: stepFor(param)
		})),
		active: (values: FilterValues) =>
			scalar.some((param) => Number(values[param.id] ?? param.default) !== defaults[param.id]),
		apply(image, values) {
			const step: Record<string, unknown> = { op: def.id };
			for (const param of scalar) step[param.id] = Number(values[param.id] ?? param.default);
			return renderRecipe(image, { steps: [step as { op: string }] }, 100);
		}
	};
}

export const ENGINE_ADJUSTMENTS: PaintFilter[] = ADJUSTMENT_OPS.flatMap((id) => {
	const def = OP_CATALOGUE.find((candidate) => candidate.id === id);
	return def ? [adjustmentFromOp(def)] : [];
});
