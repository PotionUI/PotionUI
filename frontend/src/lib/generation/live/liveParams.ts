import type { ParameterMeta } from '$lib/components/generation/parametersModel';

export const SEED_PENDING_COPY = 'Chosen when sampling starts';

const HIDDEN_KEYS = new Set(['video_director']);

function isShown(key: string, value: unknown): boolean {
	if (HIDDEN_KEYS.has(key) || key.startsWith('_')) return false;
	if (value === null || value === undefined || value === '') return false;
	if (typeof value === 'object') return false;
	return true;
}

export interface LiveParameters {
	params: Record<string, unknown>;
	meta: Record<string, ParameterMeta> | null;
}

export function liveParameters(
	formData: Record<string, unknown> | null | undefined,
	seed: number | null
): LiveParameters {
	const params: Record<string, unknown> = {};
	for (const [key, value] of Object.entries(formData ?? {})) {
		if (isShown(key, value)) params[key] = value;
	}
	const requested = typeof params.seed === 'number' ? params.seed : null;
	if (seed !== null) params.seed = seed;
	else if (requested === null || requested < 0) params.seed = SEED_PENDING_COPY;
	return { params, meta: null };
}
