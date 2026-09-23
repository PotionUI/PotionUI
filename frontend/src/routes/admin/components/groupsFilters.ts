import type { UserGroup } from '$lib/services/admin-api';
import { parseServerDate } from '$lib/utils/relativeTime';
import { createFilterCodec, type FilterFieldDescriptor } from '$lib/components/library/filterCodec';
import type { FilterChip, SortOption } from '$lib/components/library/librarySection';

export type GroupSortBy = 'name' | 'created';

export interface GroupsFilters {
	q: string;
	sortBy: GroupSortBy;
}

export const DEFAULT_GROUPS_FILTERS: GroupsFilters = {
	q: '',
	sortBy: 'name'
};

export const GROUPS_SORT_OPTIONS: readonly SortOption<GroupSortBy>[] = [
	{ value: 'name', label: 'Name' },
	{ value: 'created', label: 'Recently added' }
];

const FIELDS: readonly FilterFieldDescriptor<GroupsFilters>[] = [];

const codec = createFilterCodec<GroupsFilters>({
	defaults: DEFAULT_GROUPS_FILTERS,
	fields: FIELDS,
	sortValues: ['name', 'created']
});

const GROUP_Q_PARAM = 'group_q';
const GROUP_SORT_PARAM = 'group_sort_by';

function toInternalParams(params: URLSearchParams): URLSearchParams {
	const internal = new URLSearchParams();
	const q = params.get(GROUP_Q_PARAM);
	if (q) internal.set('q', q);
	const sortBy = params.get(GROUP_SORT_PARAM);
	if (sortBy) internal.set('sort_by', sortBy);
	return internal;
}

function toExternalParams(params: URLSearchParams): URLSearchParams {
	const external = new URLSearchParams();
	const q = params.get('q');
	if (q) external.set(GROUP_Q_PARAM, q);
	const sortBy = params.get('sort_by');
	if (sortBy) external.set(GROUP_SORT_PARAM, sortBy);
	return external;
}

export function groupsFiltersFromSearchParams(params: URLSearchParams): GroupsFilters {
	return codec.fromSearchParams(toInternalParams(params));
}

export function groupsFiltersToSearchParams(filters: GroupsFilters): URLSearchParams {
	return toExternalParams(codec.toSearchParams(filters));
}

export function groupsFilterChips(filters: GroupsFilters): FilterChip[] {
	return codec.chips(filters);
}

export function clearGroupsFilterChip(filters: GroupsFilters, key: string): GroupsFilters {
	return codec.clearChip(filters, key);
}

export function clearAllGroupsFilters(filters: GroupsFilters): GroupsFilters {
	return codec.clearAll(filters);
}

export function groupsFilterActiveCount(filters: GroupsFilters): number {
	return codec.activeCount(filters);
}

function groupTimestamp(group: UserGroup): number {
	return parseServerDate(group.created_at)?.getTime() ?? 0;
}

export function applyGroupsFilters(groups: readonly UserGroup[], filters: GroupsFilters): UserGroup[] {
	const query = filters.q.trim().toLowerCase();
	const rows = groups.filter((group) => {
		if (!query) return true;
		return group.name.toLowerCase().includes(query) || (group.description || '').toLowerCase().includes(query);
	});
	if (filters.sortBy === 'created') return rows.sort((a, b) => groupTimestamp(b) - groupTimestamp(a));
	return rows.sort((a, b) => a.name.localeCompare(b.name));
}
