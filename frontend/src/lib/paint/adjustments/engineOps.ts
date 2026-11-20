import { OPS_BY_ID, type OpSpec } from '$lib/filters/engine';
import { renderRecipe } from '$lib/filters/render';
import { scalarParams, type ScalarParam } from '$lib/filters/steps';
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

function stepFor(param: ScalarParam): number | undefined {
	return param.type === 'float' ? 0.1 : undefined;
}

export function adjustmentFromOp(def: OpSpec): PaintFilter {
	const scalar = scalarParams(def);
	const plot = def.params.some((param) => param.type === 'curve');
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
			unit: param.unit ?? undefined,
			step: stepFor(param)
		})),
		active: (values: FilterValues) =>
			scalar.some((param) => Number(values[param.id] ?? param.default) !== param.default),
		apply(image, values) {
			const step: Record<string, unknown> = { op: def.id };
			for (const param of scalar) step[param.id] = Number(values[param.id] ?? param.default);
			return renderRecipe(image, { steps: [step as { op: string }] }, 100);
		}
	};
}

export const ENGINE_ADJUSTMENTS: PaintFilter[] = ADJUSTMENT_OPS.flatMap((id) => {
	const def = OPS_BY_ID[id];
	return def ? [adjustmentFromOp(def)] : [];
});
