import type { FilterValues, PaintFilter, PixelBuffer } from '../types';
import { blendByMask } from '../raster/layerMask';

export interface FilterStep {
	filter: PaintFilter;
	values: FilterValues;
}

export function activeSteps(steps: FilterStep[]): FilterStep[] {
	return steps.filter((step) => step.filter.active(step.values));
}

export async function runFilters(
	base: PixelBuffer,
	steps: FilterStep[],
	mask?: Uint8ClampedArray | null
): Promise<PixelBuffer> {
	const active = activeSteps(steps);
	let current: PixelBuffer = {
		width: base.width,
		height: base.height,
		data: new Uint8ClampedArray(base.data)
	};
	for (const step of active) {
		current = await step.filter.apply(current, step.values);
	}
	if (active.length === 0) return current;
	if (!mask) return current;
	return {
		width: base.width,
		height: base.height,
		data: blendByMask(base.data, current.data, mask)
	};
}
