import type { PaintFilter, PixelBuffer } from '$lib/components/imageEditor/types';
import { FilterOpUnavailable, type FilterExtensions, type OpSpec, type PluginSpatialApply } from './engine';

function spatialAdapter(adjustment: PaintFilter): PluginSpatialApply {
	return (data, width, height, values, amount) => {
		const original = new Uint8ClampedArray(data);
		const result = adjustment.apply({ width, height, data: new Uint8ClampedArray(data) }, values as never);
		if (result instanceof Promise) throw new FilterOpUnavailable(adjustment.id);
		const output = (result as PixelBuffer).data;
		for (let i = 0; i < data.length; i += 4) {
			data[i] = original[i] + (output[i] - original[i]) * amount;
			data[i + 1] = original[i + 1] + (output[i + 1] - original[i + 1]) * amount;
			data[i + 2] = original[i + 2] + (output[i + 2] - original[i + 2]) * amount;
		}
	};
}

export function buildExtensions(ops: OpSpec[], adjustments: PaintFilter[]): FilterExtensions {
	const byId = new Map(adjustments.map((adjustment) => [adjustment.id, adjustment]));
	const extensions: Required<FilterExtensions> = { ops: {}, colour: {}, spatial: {} };
	for (const spec of ops) {
		if (spec.source !== 'plugin') continue;
		extensions.ops[spec.id] = spec;
		const adjustment = byId.get(spec.id);
		if (!adjustment) continue;
		if (spec.kind === 'colour' && adjustment.map) extensions.colour[spec.id] = adjustment.map;
		else if (spec.kind === 'spatial') extensions.spatial[spec.id] = spatialAdapter(adjustment);
	}
	return extensions;
}

let last: { ops: OpSpec[]; adjustments: PaintFilter[]; extensions: FilterExtensions } | null = null;

export function sharedExtensions(ops: OpSpec[], adjustments: PaintFilter[]): FilterExtensions {
	if (
		last &&
		last.ops === ops &&
		last.adjustments.length === adjustments.length &&
		last.adjustments.every((adjustment, index) => adjustment === adjustments[index])
	) {
		return last.extensions;
	}
	last = { ops, adjustments, extensions: buildExtensions(ops, adjustments) };
	return last.extensions;
}
