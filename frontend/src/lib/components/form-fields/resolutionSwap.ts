import {
	parseCustomResolution,
	type ResolutionOptionLike
} from './resolutionPicker';

export interface SwapState {
	visible: boolean;
	enabled: boolean;
	target: string | null;
	label: string;
}

export interface SwapOptions {
	allowCustom?: boolean;
	maxPixels?: number | null;
}

export function parseDims(value: string | null | undefined): { width: number; height: number } | null {
	if (!value || typeof value !== 'string') return null;
	const match = /^\s*(\d+)\s*[x×]\s*(\d+)\s*$/i.exec(value);
	if (!match) return null;
	const width = parseInt(match[1], 10);
	const height = parseInt(match[2], 10);
	if (!width || !height) return null;
	return { width, height };
}

export function mirrorResolution(value: string | null | undefined): string | null {
	const dims = parseDims(value);
	if (!dims || dims.width === dims.height) return null;
	return `${dims.height}x${dims.width}`;
}

function isSquare(value: string): boolean {
	const dims = parseDims(value);
	return !!dims && dims.width === dims.height;
}

function display(value: string): string {
	return value.replace('x', '×');
}

function findOptionValue(options: ResolutionOptionLike[], mirror: string): string | null {
	const wanted = parseDims(mirror);
	if (!wanted) return null;
	for (const opt of options) {
		const dims = parseDims(opt.value);
		if (dims && dims.width === wanted.width && dims.height === wanted.height) return opt.value;
	}
	return null;
}

export function resolveSwap(
	current: string | null | undefined,
	options: ResolutionOptionLike[],
	{ allowCustom = true, maxPixels = null }: SwapOptions = {}
): SwapState {
	const currentDims = parseDims(current);
	const hasOrientation =
		(currentDims !== null && currentDims.width !== currentDims.height) ||
		options.some((opt) => !isSquare(opt.value) && parseDims(opt.value) !== null);

	if (!hasOrientation) {
		return { visible: false, enabled: false, target: null, label: 'Swap width and height' };
	}

	const mirror = mirrorResolution(current);
	if (!currentDims) {
		return { visible: true, enabled: false, target: null, label: 'Pick a size to swap' };
	}
	if (!mirror) {
		return { visible: true, enabled: false, target: null, label: 'Square size, nothing to swap' };
	}

	const label = `Swap to ${display(mirror)}`;
	const fromList = findOptionValue(options, mirror);
	if (fromList) return { visible: true, enabled: true, target: fromList, label };

	if (allowCustom) {
		const dims = parseDims(mirror)!;
		const check = parseCustomResolution(String(dims.width), String(dims.height));
		const withinPixels = !maxPixels || dims.width * dims.height <= maxPixels;
		if (check.ok && withinPixels) return { visible: true, enabled: true, target: mirror, label };
	}

	return {
		visible: true,
		enabled: false,
		target: null,
		label: `No ${display(mirror)} option for this model`
	};
}
