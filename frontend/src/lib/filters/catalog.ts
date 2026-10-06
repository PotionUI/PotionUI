import { get, writable } from 'svelte/store';
import { api } from '$lib/services/api/index';
import { parseCube, type Cube } from './engine';
import type { FilterCatalogData, FilterDraft, FilterItem, FilterOpInfo, FilterPatch } from './types';

export interface CatalogState {
	status: 'idle' | 'loading' | 'ready' | 'error';
	items: FilterItem[];
	ops: FilterOpInfo[];
	groups: string[];
	error: string | null;
}

const EMPTY: CatalogState = { status: 'idle', items: [], ops: [], groups: [], error: null };

export const filterCatalog = writable<CatalogState>(EMPTY);

const cubes = new Map<string, Promise<Cube>>();
let pending: Promise<void> | null = null;

export function resetFilterCatalog(): void {
	filterCatalog.set(EMPTY);
	cubes.clear();
	pending = null;
}

interface ErrorDetail {
	error?: unknown;
	message?: unknown;
}

function errorDetail(error: unknown): ErrorDetail | null {
	const data = (error as { response?: { data?: { detail?: unknown } } })?.response?.data;
	const detail = data?.detail;
	return detail && typeof detail === 'object' ? (detail as ErrorDetail) : null;
}

export function errorCode(error: unknown): string | null {
	const code = errorDetail(error)?.error;
	return typeof code === 'string' ? code : null;
}

export function errorText(error: unknown, fallback: string): string {
	const message = errorDetail(error)?.message;
	if (typeof message === 'string' && message) return message;
	if (error instanceof Error && !('response' in error) && error.message) return error.message;
	return fallback;
}

export function isNameTaken(error: unknown): boolean {
	return errorCode(error) === 'filter_name_taken';
}

function apply(data: FilterCatalogData): void {
	filterCatalog.set({
		status: 'ready',
		items: data.filters,
		ops: data.ops,
		groups: data.groups,
		error: null
	});
}

export function loadFilterCatalog(force = false): Promise<void> {
	const current = get(filterCatalog);
	if (!force && current.status === 'ready') return Promise.resolve();
	if (pending) return pending;
	filterCatalog.update((state) => ({ ...state, status: 'loading', error: null }));
	pending = api
		.listFilters()
		.then(apply)
		.catch((error: unknown) => {
			filterCatalog.update((state) => ({
				...state,
				status: 'error',
				error: errorText(error, 'Filters could not be loaded.')
			}));
		})
		.finally(() => {
			pending = null;
		});
	return pending;
}

export function loadCube(item: FilterItem): Promise<Cube> {
	const key = `${item.id}@${item.revision}`;
	let cube = cubes.get(key);
	if (!cube) {
		const url = item.lut_url ?? `/api/filters/${encodeURIComponent(item.id)}/lut`;
		cube = api.getFilterLut(url).then(parseCube);
		cube.catch(() => cubes.delete(key));
		cubes.set(key, cube);
	}
	return cube;
}

export async function createMineFilter(draft: FilterDraft): Promise<FilterItem> {
	const created = await api.createMineFilter(draft);
	await loadFilterCatalog(true);
	return created;
}

export async function updateMineFilter(id: string, patch: FilterPatch): Promise<FilterItem> {
	const updated = await api.updateMineFilter(id, patch);
	await loadFilterCatalog(true);
	return updated;
}

export async function deleteMineFilter(id: string): Promise<void> {
	await api.deleteMineFilter(id);
	await loadFilterCatalog(true);
}
