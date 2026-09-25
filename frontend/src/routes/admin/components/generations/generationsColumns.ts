import type { AdminGenerationListItem } from '$lib/services/admin-api';
import { parseServerDate } from '$lib/utils/relativeTime';
import { formatDurationMs } from '$lib/components/generation-panel/barState';
import type { SortState } from '$lib/components/table';
import type { GenerationSortBy } from '../generationsFilters';

export function durationFor(row: Pick<AdminGenerationListItem, 'completed_at' | 'created_at' | 'status'>): string {
	if (!row.completed_at) return row.status === 'running' ? 'running' : '—';
	const completed = parseServerDate(row.completed_at)?.getTime();
	const created = parseServerDate(row.created_at)?.getTime();
	const ms = completed != null && created != null ? completed - created : NaN;
	return Number.isFinite(ms) && ms >= 0 ? formatDurationMs(ms) : '—';
}

export function presetTitleFor(row: Pick<AdminGenerationListItem, 'preset_name' | 'mode'>): string {
	return row.preset_name || row.mode || 'Untitled generation';
}

const SORT_COLUMN_KEY = 'created';

export function sortStateFromSortBy(sortBy: GenerationSortBy): SortState {
	return { key: SORT_COLUMN_KEY, dir: sortBy === 'created_asc' ? 'asc' : 'desc' };
}

export function sortByFromSortState(sort: SortState | null): GenerationSortBy {
	if (!sort || sort.key !== SORT_COLUMN_KEY || sort.dir === 'desc') return 'created_desc';
	return 'created_asc';
}
