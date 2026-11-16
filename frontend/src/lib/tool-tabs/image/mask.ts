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

const LEGACY_SERVE = '/api/media/serve?path=';

function uploadUrlOf(path: string): string {
	const name = path.split(/[\\/]/).filter(Boolean).pop() ?? path;
	return `/api/media/uploads/${encodeURIComponent(name)}`;
}

export function maskChannelOf(stored: { path: string; url?: string | null }, geometry: string): MaskChannel {
	return { path: stored.path, url: stored.url || uploadUrlOf(stored.path), geometry };
}

export function readMask(raw: unknown): MaskChannel | null {
	if (!raw || typeof raw !== 'object') return null;
	const { path, url, geometry } = raw as Record<string, unknown>;
	if (typeof path !== 'string' || !path || typeof url !== 'string' || typeof geometry !== 'string') return null;
	return { path, url: url.startsWith(LEGACY_SERVE) ? uploadUrlOf(path) : url, geometry };
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

export const MASK_BRUSH = { min: 4, max: 300, step: 1, default: 48 } as const;

export const STORED_MASK_CUTOFF = 127;

export function alphaToMask(source: Uint8ClampedArray, target: Uint8ClampedArray, cutoff = 64): number {
	let lit = 0;
	for (let i = 0; i < source.length; i += 4) {
		const on = source[i + 3] > cutoff;
		const value = on ? 255 : 0;
		target[i] = value;
		target[i + 1] = value;
		target[i + 2] = value;
		target[i + 3] = 255;
		if (on) lit += 1;
	}
	return lit;
}
