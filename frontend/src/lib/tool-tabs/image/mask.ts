import type { EditStep } from '$lib/tool-tabs/frame/editStack';
import { GEOMETRY_LANE } from './steps';

export interface MaskChannel {
	path: string;
	url: string;
	geometry: string;
}

export function geometryKey(steps: readonly EditStep[]): string {
	return JSON.stringify(
		steps.filter((step) => !step.hidden && step.lane === GEOMETRY_LANE).map((step) => [step.tool, step.params])
	);
}

export function liveMask(mask: MaskChannel | null, steps: readonly EditStep[]): MaskChannel | null {
	if (!mask) return null;
	return mask.geometry === geometryKey(steps) ? mask : null;
}

export function maskIsStale(mask: MaskChannel | null, steps: readonly EditStep[]): boolean {
	return mask !== null && liveMask(mask, steps) === null;
}

export function readMask(raw: unknown): MaskChannel | null {
	if (!raw || typeof raw !== 'object') return null;
	const { path, url, geometry } = raw as Record<string, unknown>;
	if (typeof path !== 'string' || !path || typeof url !== 'string' || typeof geometry !== 'string') return null;
	return { path, url, geometry };
}

export function sendPatch(
	field: string,
	item: Record<string, unknown>,
	mask: MaskChannel | null,
	allowInpaint: boolean
): Record<string, unknown> {
	return {
		[field]: item,
		[`${field}_inpaint_mask`]: allowInpaint && mask ? mask.path : undefined,
		[`${field}__origin`]: undefined
	};
}

export type Rgb = [number, number, number];

export function parseRgb(raw: string, fallback: Rgb = [255, 255, 255]): Rgb {
	const parts = raw.trim().split(/[\s,/]+/).map(Number);
	if (parts.length < 3 || parts.slice(0, 3).some((value) => !Number.isFinite(value))) return fallback;
	return [parts[0], parts[1], parts[2]];
}

export function maskOverlay(pixels: Uint8ClampedArray, tint: Rgb, cutoff = 128): { data: Uint8ClampedArray; lit: number } {
	const data = new Uint8ClampedArray(pixels.length);
	let lit = 0;
	for (let i = 0; i < pixels.length; i += 4) {
		if (pixels[i] <= cutoff) continue;
		data[i] = tint[0];
		data[i + 1] = tint[1];
		data[i + 2] = tint[2];
		data[i + 3] = 255;
		lit += 1;
	}
	return { data, lit };
}
