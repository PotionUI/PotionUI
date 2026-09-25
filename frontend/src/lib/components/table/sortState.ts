export type SortDirection = 'asc' | 'desc';

export interface SortState {
	key: string;
	dir: SortDirection;
}

export function cycleSort(current: SortState | null, key: string): SortState | null {
	if (!current || current.key !== key) return { key, dir: 'asc' };
	if (current.dir === 'asc') return { key, dir: 'desc' };
	return null;
}
