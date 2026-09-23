import type { PresetInfo } from '$lib/types/api';
import { createFilterCodec, type FilterFieldDescriptor } from '$lib/components/library/filterCodec';
import type { FilterChip, SortOption } from '$lib/components/library/librarySection';
import type { PresetLibrarySection } from './presetLibrarySections';

export type PresetInstallFilter = 'all' | 'installed' | 'not-installed';
export type PresetRequirementsFilter = 'all' | 'missing' | 'unknown';
export type PresetAssignmentFilter = 'any' | 'unassigned';
export type PresetSortBy = 'name' | 'vram';

export interface PresetFilters {
	q: string;
	engine: string;
	install: PresetInstallFilter;
	requirements: PresetRequirementsFilter;
	assignment: PresetAssignmentFilter;
	sortBy: PresetSortBy;
}

export const DEFAULT_PRESET_FILTERS: PresetFilters = {
	q: '',
	engine: '',
	install: 'all',
	requirements: 'all',
	assignment: 'any',
	sortBy: 'name'
};

export const PRESET_INSTALL_OPTIONS: ReadonlyArray<{ value: PresetInstallFilter; label: string }> = [
	{ value: 'all', label: 'All' },
	{ value: 'installed', label: 'Installed' },
	{ value: 'not-installed', label: 'Not installed' }
];

export const PRESET_REQUIREMENTS_OPTIONS: ReadonlyArray<{ value: PresetRequirementsFilter; label: string }> = [
	{ value: 'all', label: 'All' },
	{ value: 'missing', label: 'Missing' },
	{ value: 'unknown', label: 'Unknown' }
];

export const PRESET_ASSIGNMENT_OPTIONS: ReadonlyArray<{ value: PresetAssignmentFilter; label: string }> = [
	{ value: 'any', label: 'Any' },
	{ value: 'unassigned', label: 'Unassigned' }
];

export const PRESET_SORT_OPTIONS: readonly SortOption<PresetSortBy>[] = [
	{ value: 'name', label: 'Name A–Z' },
	{ value: 'vram', label: 'VRAM' }
];

const FIELDS: readonly FilterFieldDescriptor<PresetFilters>[] = [
	{
		kind: 'text',
		key: 'engine',
		param: 'engine',
		label: 'Engine',
		default: '',
		chipLabel: (value) => `engine: ${value}`
	},
	{
		kind: 'enum',
		key: 'install',
		param: 'install',
		label: 'Status',
		values: ['installed', 'not-installed'],
		default: 'all',
		chipLabel: (value) => (value === 'installed' ? 'Installed' : 'Not installed')
	},
	{
		kind: 'enum',
		key: 'requirements',
		param: 'requirements',
		label: 'Requirements',
		values: ['missing', 'unknown'],
		default: 'all',
		chipLabel: (value) => (value === 'missing' ? 'Missing requirements' : 'Unknown requirements')
	},
	{
		kind: 'enum',
		key: 'assignment',
		param: 'assignment',
		label: 'Assignment',
		values: ['unassigned'],
		default: 'any',
		chipLabel: () => 'Unassigned'
	}
];

const codec = createFilterCodec<PresetFilters>({
	defaults: DEFAULT_PRESET_FILTERS,
	fields: FIELDS,
	sortValues: ['name', 'vram']
});

export function presetFiltersFromSearchParams(params: URLSearchParams): PresetFilters {
	return codec.fromSearchParams(params);
}

export function presetFiltersToSearchParams(filters: PresetFilters): URLSearchParams {
	return codec.toSearchParams(filters);
}

export function presetFilterChips(filters: PresetFilters): FilterChip[] {
	return codec.chips(filters);
}

export function clearPresetFilterChip(filters: PresetFilters, key: string): PresetFilters {
	return codec.clearChip(filters, key);
}

export function clearAllPresetFilters(filters: PresetFilters): PresetFilters {
	return codec.clearAll(filters);
}

export function presetFilterActiveCount(filters: PresetFilters): number {
	return codec.activeCount(filters);
}

export function presetEngines(presets: readonly PresetInfo[]): string[] {
	return Array.from(
		new Set(presets.map((preset) => preset.engine).filter((engine): engine is string => !!engine))
	).sort();
}

export function presetEngineCounts(presets: readonly PresetInfo[]): Record<string, number> {
	const counts: Record<string, number> = {};
	for (const preset of presets) {
		if (!preset.engine) continue;
		counts[preset.engine] = (counts[preset.engine] ?? 0) + 1;
	}
	return counts;
}

export function presetSectionCounts(presets: readonly PresetInfo[]): Partial<Record<PresetLibrarySection, number>> {
	const counts: Partial<Record<PresetLibrarySection, number>> = { all: presets.length };
	for (const preset of presets) {
		const category = preset.category as PresetLibrarySection | undefined;
		if (!category) continue;
		counts[category] = (counts[category] ?? 0) + 1;
	}
	return counts;
}

export function presetVramLabel(preset: Pick<PresetInfo, 'requires'>): string | null {
	const vram = preset.requires?.recommended_vram_gb;
	return typeof vram === 'number' ? `${vram} GB VRAM` : null;
}

function presetVram(preset: PresetInfo): number {
	return preset.requires?.recommended_vram_gb ?? Number.POSITIVE_INFINITY;
}

function matchesQuery(preset: PresetInfo, query: string): boolean {
	if (!query) return true;
	const haystack = [preset.name, preset.id, ...(preset.tags ?? [])].join(' ').toLowerCase();
	return haystack.includes(query);
}

export function applyPresetFilters(
	presets: readonly PresetInfo[],
	filters: PresetFilters,
	section: PresetLibrarySection
): PresetInfo[] {
	const query = filters.q.trim().toLowerCase();
	const rows = presets.filter((preset) => {
		if (section !== 'all' && preset.category !== section) return false;
		if (filters.engine && preset.engine !== filters.engine) return false;
		if (filters.install === 'installed' && !preset.installed) return false;
		if (filters.install === 'not-installed' && preset.installed) return false;
		if (filters.requirements === 'missing' && !(preset.requirements_summary?.missing ?? 0)) return false;
		if (filters.requirements === 'unknown' && !(preset.requirements_summary?.unknown ?? 0)) return false;
		if (
			filters.assignment === 'unassigned' &&
			((preset.assignment_count ?? 0) > 0 || (preset.group_count ?? 0) > 0)
		)
			return false;
		return matchesQuery(preset, query);
	});
	if (filters.sortBy === 'vram') {
		return rows.sort((a, b) => presetVram(a) - presetVram(b) || a.name.localeCompare(b.name));
	}
	return rows.sort((a, b) => a.name.localeCompare(b.name));
}
