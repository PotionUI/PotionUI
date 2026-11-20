import { persistedStore } from './persistedStore';

export type PhrasebookSortBy = 'path' | 'name' | 'updated';
export type PhrasebookSortDir = 'asc' | 'desc';

export interface PhrasebookSort {
	by: PhrasebookSortBy;
	dir: PhrasebookSortDir;
}

export const PHRASEBOOK_SORT_DEFAULT_DIR: Record<PhrasebookSortBy, PhrasebookSortDir> = {
	path: 'asc',
	name: 'asc',
	updated: 'desc'
};

const DEFAULT_SORT: PhrasebookSort = { by: 'path', dir: 'asc' };

function parseSort(raw: string): PhrasebookSort {
	const value = JSON.parse(raw) as Partial<PhrasebookSort>;
	const by: PhrasebookSortBy = value.by === 'name' || value.by === 'updated' ? value.by : 'path';
	const dir: PhrasebookSortDir = value.dir === 'desc' ? 'desc' : value.dir === 'asc' ? 'asc' : PHRASEBOOK_SORT_DEFAULT_DIR[by];
	return { by, dir };
}

export const phrasebookSort = persistedStore<PhrasebookSort>(
	'phrasebook-category-sort',
	parseSort,
	DEFAULT_SORT,
	JSON.stringify
);

interface Sortable {
	name: string;
	updated_at?: string;
}

export function sortCategories<T extends Sortable>(list: readonly T[], sort: PhrasebookSort): T[] {
	const sign = sort.dir === 'desc' ? -1 : 1;
	if (sort.by === 'path') return sort.dir === 'desc' ? [...list].reverse() : [...list];
	if (sort.by === 'updated') {
		const stamp = (item: T) => Date.parse(item.updated_at ?? '') || 0;
		return [...list].sort((a, b) => sign * (stamp(a) - stamp(b)));
	}
	return [...list].sort((a, b) => sign * a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
}
