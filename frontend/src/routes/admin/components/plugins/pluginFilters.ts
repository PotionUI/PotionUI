import { createFilterCodec, type FilterFieldDescriptor } from '$lib/components/library/filterCodec';
import type { FilterChip, SortOption } from '$lib/components/library/librarySection';
import { pluginCategories, resolveCategory } from '$lib/plugins/categories';
import type { Plugin } from '$lib/stores/plugins';

export type PluginStateFilter = '' | 'enabled' | 'disabled' | 'error';
export type PluginTypeFilter = '' | 'full-stack' | 'backend-only' | 'frontend-only';
export type PluginSortBy = 'name' | 'category' | 'state';

export interface PluginFilters {
	q: string;
	state: PluginStateFilter;
	type: PluginTypeFilter;
	sortBy: PluginSortBy;
}

export const DEFAULT_PLUGIN_FILTERS: PluginFilters = { q: '', state: '', type: '', sortBy: 'name' };

export const PLUGIN_SORT_OPTIONS: ReadonlyArray<SortOption<PluginSortBy>> = [
	{ value: 'name', label: 'Name' },
	{ value: 'category', label: 'Category' },
	{ value: 'state', label: 'State' }
];

export const PLUGIN_STATE_OPTIONS: ReadonlyArray<{ value: PluginStateFilter; label: string }> = [
	{ value: '', label: 'All' },
	{ value: 'enabled', label: 'Enabled' },
	{ value: 'disabled', label: 'Disabled' },
	{ value: 'error', label: 'Errored' }
];

export const PLUGIN_TYPE_OPTIONS: ReadonlyArray<{ value: PluginTypeFilter; label: string }> = [
	{ value: '', label: 'All types' },
	{ value: 'full-stack', label: 'Full-stack' },
	{ value: 'backend-only', label: 'Backend-only' },
	{ value: 'frontend-only', label: 'Frontend-only' }
];

const FIELDS: readonly FilterFieldDescriptor<PluginFilters>[] = [
	{
		kind: 'enum',
		key: 'state',
		param: 'state',
		label: 'State',
		values: ['enabled', 'disabled', 'error'],
		default: '',
		chipLabel: (value) => PLUGIN_STATE_OPTIONS.find((option) => option.value === value)?.label ?? value
	},
	{
		kind: 'enum',
		key: 'type',
		param: 'type',
		label: 'Type',
		values: ['full-stack', 'backend-only', 'frontend-only'],
		default: '',
		chipLabel: (value) => PLUGIN_TYPE_OPTIONS.find((option) => option.value === value)?.label ?? value
	}
];

const codec = createFilterCodec<PluginFilters>({
	defaults: DEFAULT_PLUGIN_FILTERS,
	fields: FIELDS,
	sortValues: ['name', 'category', 'state']
});

export function pluginFiltersFromSearchParams(params: URLSearchParams): PluginFilters {
	return codec.fromSearchParams(params);
}

export function pluginFiltersToSearchParams(filters: PluginFilters): URLSearchParams {
	return codec.toSearchParams(filters);
}

export function pluginFilterActiveCount(filters: PluginFilters): number {
	return codec.activeCount(filters);
}

export function pluginFilterChips(filters: PluginFilters): FilterChip[] {
	return codec.chips(filters);
}

export function clearPluginFilterChip(filters: PluginFilters, key: string): PluginFilters {
	return codec.clearChip(filters, key);
}

export function clearAllPluginFilters(filters: PluginFilters): PluginFilters {
	return codec.clearAll(filters);
}

function matchesSearch(plugin: Plugin, query: string): boolean {
	if (!query) return true;
	const haystack = [plugin.name, plugin.id, plugin.description ?? '', ...(plugin.tags ?? [])]
		.join(' ')
		.toLowerCase();
	return haystack.includes(query);
}

function statusRank(plugin: Plugin): number {
	if (plugin.state === 'error') return 2;
	return plugin.enabled ? 0 : 1;
}

function byName(a: Plugin, b: Plugin): number {
	return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
}

export function applyPluginFilters(plugins: readonly Plugin[], section: string, filters: PluginFilters): Plugin[] {
	const query = filters.q.trim().toLowerCase();
	const matched = plugins.filter((plugin) => {
		if (section !== 'all' && resolveCategory(plugin.category).id !== section) return false;
		if (filters.state === 'enabled' && !(plugin.enabled && plugin.state !== 'error')) return false;
		if (filters.state === 'disabled' && (plugin.enabled || plugin.state === 'error')) return false;
		if (filters.state === 'error' && plugin.state !== 'error') return false;
		if (filters.type && plugin.type !== filters.type) return false;
		return matchesSearch(plugin, query);
	});
	if (filters.sortBy === 'category') {
		return matched.sort(
			(a, b) =>
				resolveCategory(a.category).label.localeCompare(resolveCategory(b.category).label) || byName(a, b)
		);
	}
	if (filters.sortBy === 'state') {
		return matched.sort((a, b) => statusRank(a) - statusRank(b) || byName(a, b));
	}
	return matched.sort(byName);
}

export function pluginCategoryCounts(plugins: readonly Plugin[]): Record<string, number> {
	const counts: Record<string, number> = { all: plugins.length };
	for (const category of pluginCategories) counts[category.id] = 0;
	for (const plugin of plugins) {
		const id = resolveCategory(plugin.category).id;
		counts[id] = (counts[id] ?? 0) + 1;
	}
	return counts;
}
