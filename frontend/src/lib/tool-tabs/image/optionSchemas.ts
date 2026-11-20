import type { FieldConfig } from '$lib/form/reactions';
import { withRectField, type RectField } from './cropActions';
import type { PixelSize } from './payload';
import { resizeOutput } from './payload';
import { MAX_GRID, type SliceAspect, type SliceParams } from './slice/model';
import type { CanvasOverlayEntry, OverlayMode } from '$lib/editors/overlays';
import type { CropParams, ResizeParams } from './steps';

const field = (config: Record<string, unknown>) => config as unknown as FieldConfig;

const pair = (name: string, children: FieldConfig[]) => field({ type: 'row', name, configuration: { columns: 2 }, children });

const pixelBox = (name: string, title: string, extra: Record<string, unknown> = {}) =>
	field({ type: 'number_box', name, title, integer: true, minimum: 0, placeholder: '', ...extra });

export const RESIZE_MAX_SIDE = 16384;

export function clampSide(value: number): number {
	return Math.max(1, Math.min(RESIZE_MAX_SIDE, Math.round(value)));
}

export function resizeFields(): FieldConfig[] {
	return [
		pair('resize_size', [
			pixelBox('width', 'Width', { minimum: 1, maximum: RESIZE_MAX_SIDE }),
			pixelBox('height', 'Height', { minimum: 1, maximum: RESIZE_MAX_SIDE })
		]),
		field({ type: 'checkbox', name: 'lock', title: 'Keep aspect ratio', default: true })
	];
}

export function resizeValues(params: ResizeParams, input: PixelSize): Record<string, unknown> {
	const output = resizeOutput(params, input);
	return { width: output.width, height: output.height, lock: params.lock };
}

export function resizeFromSide(params: ResizeParams, input: PixelSize, side: 'width' | 'height', raw: unknown): ResizeParams {
	const value = clampSide(Number(raw) || 1);
	if (side === 'width') {
		return { ...params, width: value, height: params.lock ? Math.max(1, Math.round((value * input.height) / Math.max(1, input.width))) : params.height };
	}
	return { ...params, height: value, width: params.lock ? Math.max(1, Math.round((value * input.width) / Math.max(1, input.height))) : params.width };
}

export function resizeFromScale(params: ResizeParams, input: PixelSize, percent: number): ResizeParams {
	const width = clampSide((input.width * percent) / 100);
	return { ...params, lock: true, width, height: Math.max(1, Math.round((width * input.height) / Math.max(1, input.width))) };
}

export const CROP_FULL = 'full';

export function cropAspectFields(aspects: readonly { key: string; label: string }[]): FieldConfig[] {
	return [field({ type: 'segmented', name: 'aspect', title: 'Aspect', options: aspects.map((aspect) => ({ label: aspect.label, value: aspect.key })), default: 'free' })];
}

export function cropRectFields(): FieldConfig[] {
	return [
		pair('crop_origin', [pixelBox('x', 'X', { aria_label: 'Crop X' }), pixelBox('y', 'Y', { aria_label: 'Crop Y' })]),
		pair('crop_extent', [pixelBox('width', 'W', { aria_label: 'Crop width', minimum: 1 }), pixelBox('height', 'H', { aria_label: 'Crop height', minimum: 1 })])
	];
}

export function cropLongestFields(sides: readonly number[]): FieldConfig[] {
	return [
		field({
			type: 'segmented',
			name: 'longest',
			title: 'Longest side',
			options: [{ label: 'FULL', value: CROP_FULL }, ...sides.map((side) => ({ label: String(side), value: side }))],
			default: CROP_FULL
		})
	];
}

export function cropValues(params: CropParams, pixels: Record<RectField, number>): Record<string, unknown> {
	return { aspect: params.aspect, ...pixels, longest: params.plan.targetLongestSide ?? CROP_FULL };
}

export function cropFromRect(params: CropParams, frame: PixelSize, name: string, raw: unknown): CropParams | null {
	if (name !== 'x' && name !== 'y' && name !== 'width' && name !== 'height') return null;
	const pixels = Number(raw);
	if (raw === null || raw === '' || !Number.isFinite(pixels)) return null;
	return withRectField(params, frame, name, pixels);
}

export function cropFromLongest(params: CropParams, raw: unknown): CropParams {
	return { ...params, plan: { ...params.plan, targetLongestSide: raw === CROP_FULL || typeof raw !== 'number' ? null : raw } };
}

export const SLICE_NO_COLLECTION = 'none';

export const SLICE_ASPECTS: ReadonlyArray<{ label: string; value: SliceAspect }> = [
	{ label: 'Keep', value: 'keep' },
	{ label: 'Square', value: 'square' },
	{ label: 'Portrait 2:3', value: 'portrait' }
];

export function sliceGridFields(locked = false): FieldConfig[] {
	return [
		pair('slice_grid', [
			pixelBox('cols', 'Columns', { minimum: 1, maximum: MAX_GRID, disabled: locked }),
			pixelBox('rows', 'Rows', { minimum: 1, maximum: MAX_GRID, disabled: locked })
		]),
		field({ type: 'checkbox', name: 'snap', title: 'Snap to gutters', description: 'Hold Alt to move a line freely', default: true, disabled: locked })
	];
}

export function sliceCellsFields(locked = false): FieldConfig[] {
	return [
		field({ type: 'checkbox', name: 'trim', title: 'Trim background', default: false, disabled: locked }),
		pair('slice_trim', [
			pixelBox('tolerance', 'Tolerance', { minimum: 0, maximum: 64, disabled: locked }),
			pixelBox('padding', 'Padding, px', { minimum: 0, maximum: 256, disabled: locked })
		]),
		field({ type: 'segmented', name: 'aspect', title: 'Aspect for every cell', options: SLICE_ASPECTS, default: 'keep', disabled: locked })
	];
}

export function sliceNameFields(locked = false): FieldConfig[] {
	return [field({ type: 'string', name: 'pattern', title: 'Name pattern', mono: true, disabled: locked })];
}

export function sliceSaveFields(collections: readonly { value: string; label: string }[], locked = false): FieldConfig[] {
	return [
		field({
			type: 'select',
			name: 'collection',
			title: 'Save to',
			options: [{ value: SLICE_NO_COLLECTION, label: 'No collection' }, ...collections],
			default: SLICE_NO_COLLECTION,
			disabled: locked
		})
	];
}

export function sliceValues(params: SliceParams): Record<string, unknown> {
	return {
		cols: params.layout.cols,
		rows: params.layout.rows,
		snap: params.layout.snap,
		trim: params.output.trim,
		tolerance: params.output.tolerance,
		padding: params.output.padding,
		aspect: params.output.aspect,
		pattern: params.pattern,
		collection: params.collection ?? SLICE_NO_COLLECTION
	};
}

export function clampInt(raw: unknown, min: number, max: number): number | null {
	const value = Number(raw);
	if (raw === null || raw === '' || !Number.isFinite(value)) return null;
	return Math.max(min, Math.min(max, Math.round(value)));
}

export function maskBrushFields(active: CanvasOverlayEntry | null): FieldConfig[] {
	const size = active?.size;
	if (!size) return [];
	return [
		field({ type: 'slider', name: 'brush', title: size.label, minimum: size.min, maximum: size.max, step: size.step, default: size.default, unit: size.unit, reset: true, legend: false })
	];
}

export function maskValues(active: CanvasOverlayEntry | null, mode: OverlayMode | null, brush: number): Record<string, unknown> {
	return { tool: active?.id ?? '', mode: mode?.id ?? '', brush };
}
