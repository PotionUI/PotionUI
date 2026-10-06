import type { FilterValues, PaintFilter, PixelBuffer } from '../types';
import { applyGrayscale, applyInvert, applyTone } from './kernels';
import { ENGINE_ADJUSTMENTS } from './engineOps';

function copy(image: PixelBuffer): PixelBuffer {
	return { width: image.width, height: image.height, data: new Uint8ClampedArray(image.data) };
}

function num(values: FilterValues, key: string): number {
	const value = values[key];
	return typeof value === 'number' ? value : 0;
}

export const toneFilter: PaintFilter = {
	id: 'tone',
	label: 'Tone',
	params: [
		{ id: 'brightness', label: 'Brightness', min: -100, max: 100, value: 0 },
		{ id: 'contrast', label: 'Contrast', min: -100, max: 100, value: 0 },
		{ id: 'saturation', label: 'Saturation', min: -100, max: 100, value: 0 },
		{ id: 'hue', label: 'Hue', min: -180, max: 180, value: 0, unit: '°' }
	],
	active: (values) =>
		num(values, 'brightness') !== 0 ||
		num(values, 'contrast') !== 0 ||
		num(values, 'saturation') !== 0 ||
		num(values, 'hue') !== 0,
	apply(image, values) {
		const out = copy(image);
		applyTone(out.data, {
			brightness: num(values, 'brightness'),
			contrast: num(values, 'contrast'),
			saturation: num(values, 'saturation'),
			hue: num(values, 'hue')
		});
		return out;
	}
};

export const invertFilter: PaintFilter = {
	id: 'invert',
	label: 'Invert',
	params: [],
	toggle: true,
	active: (values) => values.on === true,
	apply(image) {
		const out = copy(image);
		applyInvert(out.data);
		return out;
	}
};

export const grayscaleFilter: PaintFilter = {
	id: 'grayscale',
	label: 'Grayscale',
	params: [],
	toggle: true,
	active: (values) => values.on === true,
	apply(image) {
		const out = copy(image);
		applyGrayscale(out.data);
		return out;
	}
};

export const BUILTIN_FILTERS: PaintFilter[] = [
	toneFilter,
	...ENGINE_ADJUSTMENTS,
	invertFilter,
	grayscaleFilter
];

export function defaultFilterValues(filter: PaintFilter): FilterValues {
	if (filter.toggle) return { on: false };
	const values: FilterValues = {};
	for (const param of filter.params) values[param.id] = param.value;
	return values;
}
