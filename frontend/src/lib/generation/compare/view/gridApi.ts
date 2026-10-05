import { writable } from 'svelte/store';
import { api } from '$lib/services/api/index';
import type { ActiveGrid } from '../compareStore.svelte';
import { GRIDS_BASE, gridFromApi, type ApiGrid } from './gridModel';

export const openGridId = writable<string | null>(null);

export async function fetchGrid(gridId: string): Promise<ActiveGrid> {
	const response = await api.getClient().get(`${GRIDS_BASE}/${gridId}`);
	const body = response.data as { success?: boolean; data?: ApiGrid; message?: string };
	if (!body.success || !body.data) throw new Error(body.message || 'Could not load the grid');
	return gridFromApi(body.data);
}

export async function retryGridFailed(gridId: string): Promise<ActiveGrid> {
	const response = await api.getClient().post(`${GRIDS_BASE}/${gridId}/retry-failed`);
	const body = response.data as { success?: boolean; data?: ApiGrid; message?: string };
	if (!body.success || !body.data) throw new Error(body.message || 'Could not retry the failed cells');
	return gridFromApi(body.data);
}
