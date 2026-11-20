import type { FilterChip } from './librarySection';

export const ACTIVE_FILTERS_KEY = Symbol('active-filters');

export interface ActiveFiltersContext {
	chips: () => readonly FilterChip[];
	remove: (key: string) => void;
}
