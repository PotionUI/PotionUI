import { api } from '$lib/services/api/index';
import type { GenerationRequest } from '$lib/services/api/index';
import { DEFAULT_GRID_SETTINGS, type CompareAxis, type GridSettings, type ServerGrid } from './types';

const BASE = '/api/generations/grids';

type Envelope<T> = { success: boolean; data?: T; error?: string; message?: string };

function unwrap<T>(body: Envelope<T> | undefined, fallback: string): T {
	if (!body?.success || body.data === undefined) {
		throw new Error(body?.error || body?.message || fallback);
	}
	return body.data;
}

export async function postGrid(body: {
	request: GenerationRequest;
	x_axis: CompareAxis;
	y_axis: CompareAxis | null;
	lock_seed: boolean;
}): Promise<ServerGrid> {
	const response = await api.getClient().post<Envelope<ServerGrid>>(BASE, body);
	return unwrap(response.data, 'Could not start the comparison.');
}

export async function fetchGrid(gridId: string): Promise<ServerGrid> {
	const response = await api.getClient().get<Envelope<ServerGrid>>(`${BASE}/${encodeURIComponent(gridId)}`);
	return unwrap(response.data, 'Could not load the comparison.');
}

export async function postRetryFailed(gridId: string): Promise<ServerGrid> {
	const response = await api
		.getClient()
		.post<Envelope<ServerGrid>>(`${BASE}/${encodeURIComponent(gridId)}/retry-failed`);
	return unwrap(response.data, 'Could not retry the failed cells.');
}

export async function removeGrid(gridId: string): Promise<void> {
	await api.getClient().delete(`${BASE}/${encodeURIComponent(gridId)}`);
}

export async function fetchGridSettings(): Promise<GridSettings> {
	try {
		const response = await api.getClient().get<Envelope<Partial<GridSettings>>>(`${BASE}/settings`);
		const data = response.data?.success ? response.data.data : undefined;
		return {
			confirm_above: Number(data?.confirm_above) > 0 ? Number(data?.confirm_above) : DEFAULT_GRID_SETTINGS.confirm_above,
			hard_cap: Number(data?.hard_cap) > 0 ? Number(data?.hard_cap) : DEFAULT_GRID_SETTINGS.hard_cap
		};
	} catch {
		return { ...DEFAULT_GRID_SETTINGS };
	}
}
