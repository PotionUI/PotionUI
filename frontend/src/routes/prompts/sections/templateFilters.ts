import type { SegmentTemplate } from '$lib/types/segments';
import { parseServerDate } from '$lib/utils/relativeTime';
import { oneOf, type FilterChip, type SortOption } from '../library/librarySection';

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

const SLOT_VALUES: Exclude<TemplateSlotsFilter, ''>[] = ['1', '2-3', '4+'];
const SORT_VALUES: TemplateSortBy[] = ['name', 'created'];

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

export function templateFiltersFromSearchParams(params: URLSearchParams): TemplateFilters {
	return {
		q: params.get('q') ?? DEFAULT_TEMPLATE_FILTERS.q,
		tags: (params.get('tags') ?? '')
			.split(',')
			.map((tag) => tag.trim())
			.filter(Boolean),
		slots: oneOf(params.get('slots'), SLOT_VALUES, DEFAULT_TEMPLATE_FILTERS.slots),
		sortBy: oneOf(params.get('sort_by'), SORT_VALUES, DEFAULT_TEMPLATE_FILTERS.sortBy)
	};
}

export function templateFiltersToSearchParams(filters: TemplateFilters): URLSearchParams {
	const params = new URLSearchParams();
	if (filters.q) params.set('q', filters.q);
	if (filters.tags.length) params.set('tags', filters.tags.join(','));
	if (filters.slots) params.set('slots', filters.slots);
	if (filters.sortBy !== DEFAULT_TEMPLATE_FILTERS.sortBy) params.set('sort_by', filters.sortBy);
	return params;
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

export function templateFilterChips(filters: TemplateFilters): FilterChip[] {
	const chips: FilterChip[] = [];
	if (filters.slots) chips.push({ key: 'slots', label: `${filters.slots} slot${filters.slots === '1' ? '' : 's'}` });
	for (const tag of filters.tags) chips.push({ key: `tag:${tag}`, label: `#${tag}` });
	return chips;
}

export function clearTemplateFilterChip(filters: TemplateFilters, key: string): TemplateFilters {
	if (key === 'slots') return { ...filters, slots: '' };
	if (key.startsWith('tag:')) {
		const tag = key.slice('tag:'.length);
		return { ...filters, tags: filters.tags.filter((entry) => entry !== tag) };
	}
	return filters;
}

export function clearAllTemplateFilters(filters: TemplateFilters): TemplateFilters {
	return { ...DEFAULT_TEMPLATE_FILTERS, q: filters.q, sortBy: filters.sortBy };
}

export function templateFilterActiveCount(filters: TemplateFilters): number {
	let count = 0;
	if (filters.slots) count++;
	if (filters.tags.length) count++;
	return count;
}
