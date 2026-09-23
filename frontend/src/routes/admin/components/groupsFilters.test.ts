import { describe, it, expect } from 'vitest';
import type { UserGroup } from '$lib/services/admin-api';
import {
	DEFAULT_GROUPS_FILTERS,
	applyGroupsFilters,
	clearAllGroupsFilters,
	clearGroupsFilterChip,
	groupsFilterActiveCount,
	groupsFilterChips,
	groupsFiltersFromSearchParams,
	groupsFiltersToSearchParams,
	type GroupsFilters
} from './groupsFilters';

function group(overrides: Partial<UserGroup> = {}): UserGroup {
	return {
		id: 'g1',
		name: 'Editors',
		description: '',
		created_at: '2026-09-01T00:00:00.000Z',
		...overrides
	};
}

describe('applyGroupsFilters', () => {
	const editors = group({ id: '1', name: 'Editors', description: 'Can edit presets', created_at: '2026-09-01T00:00:00.000Z' });
	const viewers = group({ id: '2', name: 'Viewers', description: 'Read only', created_at: '2026-09-03T00:00:00.000Z' });
	const admins = group({ id: '3', name: 'Admins', description: '', created_at: '2026-09-02T00:00:00.000Z' });
	const all = [editors, viewers, admins];

	it('passes everything through and sorts by name when filters are default', () => {
		expect(applyGroupsFilters(all, DEFAULT_GROUPS_FILTERS).map((g) => g.id)).toEqual(['3', '1', '2']);
	});

	it('matches the search against name and description', () => {
		expect(applyGroupsFilters(all, { ...DEFAULT_GROUPS_FILTERS, q: 'viewers' }).map((g) => g.id)).toEqual(['2']);
		expect(applyGroupsFilters(all, { ...DEFAULT_GROUPS_FILTERS, q: 'edit presets' }).map((g) => g.id)).toEqual(['1']);
	});

	it('sorts by recently added when sortBy is created', () => {
		expect(applyGroupsFilters(all, { ...DEFAULT_GROUPS_FILTERS, sortBy: 'created' }).map((g) => g.id)).toEqual(['2', '3', '1']);
	});

	it('does not mutate the list it was given', () => {
		const source = [...all];
		applyGroupsFilters(source, { ...DEFAULT_GROUPS_FILTERS, sortBy: 'created' });
		expect(source.map((g) => g.id)).toEqual(all.map((g) => g.id));
	});
});

describe('groupsFiltersFromSearchParams / groupsFiltersToSearchParams', () => {
	it('round-trips a non-default filter set using the group_ prefixed params', () => {
		const filters: GroupsFilters = { q: 'editors', sortBy: 'created' };
		const params = groupsFiltersToSearchParams(filters);
		expect(params.get('group_q')).toBe('editors');
		expect(params.get('group_sort_by')).toBe('created');
		expect(params.has('q')).toBe(false);
		expect(params.has('sort_by')).toBe(false);
		expect(groupsFiltersFromSearchParams(params)).toEqual(filters);
	});

	it('serializes the default filters to an empty query string', () => {
		expect(groupsFiltersToSearchParams(DEFAULT_GROUPS_FILTERS).toString()).toBe('');
	});

	it('ignores an unprefixed q/sort_by belonging to the users list', () => {
		const params = new URLSearchParams('q=alice&sort_by=created&group_q=&group_sort_by=');
		expect(groupsFiltersFromSearchParams(params)).toEqual(DEFAULT_GROUPS_FILTERS);
	});

	it('falls back to defaults for an unknown group_sort_by', () => {
		const params = new URLSearchParams('group_sort_by=bogus');
		expect(groupsFiltersFromSearchParams(params)).toEqual(DEFAULT_GROUPS_FILTERS);
	});
});

describe('groups filter chips', () => {
	it('emits no chips and zero active count for the default filters', () => {
		expect(groupsFilterChips(DEFAULT_GROUPS_FILTERS)).toEqual([]);
		expect(groupsFilterActiveCount(DEFAULT_GROUPS_FILTERS)).toBe(0);
	});

	it('has no filter fields to clear beyond search and sort', () => {
		const filters: GroupsFilters = { q: 'keep me', sortBy: 'created' };
		expect(clearGroupsFilterChip(filters, 'nothing')).toBe(filters);
		expect(clearAllGroupsFilters(filters)).toEqual({ q: 'keep me', sortBy: 'created' });
	});
});
