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
