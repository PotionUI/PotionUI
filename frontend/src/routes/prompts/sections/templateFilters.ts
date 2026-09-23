import type { SegmentTemplate } from '$lib/types/segments';
import { parseServerDate } from '$lib/utils/relativeTime';
import { createFilterCodec, type FilterFieldDescriptor } from '$lib/components/library/filterCodec';
import type { FilterChip, SortOption } from '$lib/components/library/librarySection';

export type TemplateSlotsFilter = '' | '1' | '2-3' | '4+';
export type TemplateSortBy = 'name' | 'created';

export interface TemplateFilters {
	q: string;
	tags: string[];
	slots: TemplateSlotsFilter;
	sortBy: TemplateSortBy;
}

export const DEFAULT_TEMPLATE_FILTERS: TemplateFilters = {
	q: '',
	tags: [],
	slots: '',
	sortBy: 'name'
};

export const TEMPLATE_SLOT_OPTIONS: ReadonlyArray<{ value: TemplateSlotsFilter; label: string }> = [
	{ value: '', label: 'Any' },
	{ value: '1', label: '1' },
	{ value: '2-3', label: '2–3' },
	{ value: '4+', label: '4+' }
];

export const TEMPLATE_SORT_OPTIONS: readonly SortOption<TemplateSortBy>[] = [
	{ value: 'name', label: 'Name' },
	{ value: 'created', label: 'Created' }
];

const FIELDS: readonly FilterFieldDescriptor<TemplateFilters>[] = [
	{
		kind: 'enum',
		key: 'slots',
		param: 'slots',
		label: 'Slots',
		values: ['1', '2-3', '4+'],
		default: '',
		chipLabel: (value) => `${value} slot${value === '1' ? '' : 's'}`
	},
	{ kind: 'tags', key: 'tags', param: 'tags', label: 'Tags' }
];

const codec = createFilterCodec<TemplateFilters>({
	defaults: DEFAULT_TEMPLATE_FILTERS,
	fields: FIELDS,
	sortValues: ['name', 'created']
});

export function templateFiltersFromSearchParams(params: URLSearchParams): TemplateFilters {
	return codec.fromSearchParams(params);
}

export function templateFiltersToSearchParams(filters: TemplateFilters): URLSearchParams {
	return codec.toSearchParams(filters);
}

export function templateFilterChips(filters: TemplateFilters): FilterChip[] {
	return codec.chips(filters);
}

export function clearTemplateFilterChip(filters: TemplateFilters, key: string): TemplateFilters {
	return codec.clearChip(filters, key);
}

export function clearAllTemplateFilters(filters: TemplateFilters): TemplateFilters {
	return codec.clearAll(filters);
}

export function templateFilterActiveCount(filters: TemplateFilters): number {
	return codec.activeCount(filters);
}

export function templateSlotCount(template: Pick<SegmentTemplate, 'segments'>): number {
	return template.segments?.length ?? 0;
}

export function templateSlotLabels(template: Pick<SegmentTemplate, 'segments'>): string[] {
	return (template.segments ?? []).map((segment, index) => segment.name?.trim() || `Slot ${index + 1}`);
}

export function templateTagVocabulary(
	templates: readonly SegmentTemplate[]
): Array<{ tag: string; count: number }> {
	const counts = new Map<string, number>();
	for (const template of templates) {
		for (const tag of template.tags ?? []) {
			const trimmed = tag.trim();
			if (trimmed) counts.set(trimmed, (counts.get(trimmed) ?? 0) + 1);
		}
	}
	return Array.from(counts, ([tag, count]) => ({ tag, count })).sort(
		(a, b) => b.count - a.count || a.tag.localeCompare(b.tag)
	);
}

function matchesSlots(count: number, slots: TemplateSlotsFilter): boolean {
	if (!slots) return true;
	if (slots === '1') return count === 1;
	if (slots === '2-3') return count >= 2 && count <= 3;
	return count >= 4;
}

function templateTimestamp(template: SegmentTemplate): number {
	return parseServerDate(template.updated_at ?? template.created_at)?.getTime() ?? 0;
}

export function applyTemplateFilters(
	templates: readonly SegmentTemplate[],
	filters: TemplateFilters
): SegmentTemplate[] {
	const query = filters.q.trim().toLowerCase();
	const rows = templates.filter((template) => {
		if (!matchesSlots(templateSlotCount(template), filters.slots)) return false;
		if (filters.tags.length && !filters.tags.some((tag) => (template.tags ?? []).includes(tag))) return false;
		if (!query) return true;
		const haystack = [
			template.name,
			template.description ?? '',
			...(template.tags ?? []),
			...templateSlotLabels(template)
		]
			.join(' ')
			.toLowerCase();
		return haystack.includes(query);
	});
	if (filters.sortBy === 'created') return rows.sort((a, b) => templateTimestamp(b) - templateTimestamp(a));
	return rows.sort((a, b) => a.name.localeCompare(b.name));
}
