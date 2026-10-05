import { writable } from 'svelte/store';
import type { ActiveGrid } from '../compareStore.svelte';
import { fetchGrid as fetchServerGrid, postRetryFailed } from '../compareApi';
import { gridFromServer } from '../serverGrid';

export const openGridId = writable<string | null>(null);

export async function fetchGrid(gridId: string): Promise<ActiveGrid> {
	return gridFromServer(await fetchServerGrid(gridId));
}

export async function retryGridFailed(gridId: string): Promise<ActiveGrid> {
	return gridFromServer(await postRetryFailed(gridId));
}
