import type { PixelBuffer } from '$lib/components/imageEditor/types';
import { applyFilter, type FilterRecipe, type FilterStepSpec } from './engine';

export type { FilterRecipe, FilterStepSpec };

export function toImageData(buffer: PixelBuffer): ImageData {
	if (typeof ImageData === 'undefined') {
		return { data: buffer.data, width: buffer.width, height: buffer.height, colorSpace: 'srgb' } as ImageData;
	}
	return new ImageData(buffer.data as Uint8ClampedArray<ArrayBuffer>, buffer.width, buffer.height);
}

export function renderRecipe(buffer: PixelBuffer, recipe: FilterRecipe, intensity: number): PixelBuffer {
	const source = toImageData({
		width: buffer.width,
		height: buffer.height,
		data: new Uint8ClampedArray(buffer.data)
	});
	const output = applyFilter(source, recipe, Math.min(1, Math.max(0, intensity / 100)));
	return { width: output.width, height: output.height, data: output.data };
}
