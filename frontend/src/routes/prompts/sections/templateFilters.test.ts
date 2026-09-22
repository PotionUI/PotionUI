import { describe, it, expect } from 'vitest';
import type { RichSegment, SegmentTemplate } from '$lib/types/segments';
import {
	DEFAULT_TEMPLATE_FILTERS,
	applyTemplateFilters,
	clearAllTemplateFilters,
	clearTemplateFilterChip,
	templateFilterActiveCount,
	templateFilterChips,
	templateFiltersFromSearchParams,
	templateFiltersToSearchParams,
	templateSlotLabels,
	templateTagVocabulary,
	type TemplateFilters
} from './templateFilters';

function slot(name: string | null, content = ''): RichSegment {
	return { type: 'content', content, chips: {}, enabled: true, name };
}

function template(overrides: Partial<SegmentTemplate> = {}): SegmentTemplate {
	return {
		id: 'template-1',
		name: 'Portrait base',
		description: 'A three slot portrait layout',
		segments: [slot('Subject'), slot('Lighting'), slot('Camera')],
		tags: ['portrait', 'lighting'],
		created_at: '2026-09-01T00:00:00.000Z',
		updated_at: '2026-09-01T00:00:00.000Z',
		...overrides
	};
}

describe('templateFiltersFromSearchParams / templateFiltersToSearchParams', () => {
	it('round-trips a fully-set filter state through the URL', () => {
		const filters: TemplateFilters = {
			q: 'portrait',
			tags: ['portrait', 'lighting'],
			slots: '2-3',
			sortBy: 'created'
		};
		const params = templateFiltersToSearchParams(filters);
		expect(params.get('q')).toBe('portrait');
		expect(params.get('tags')).toBe('portrait,lighting');
		expect(params.get('slots')).toBe('2-3');
		expect(params.get('sort_by')).toBe('created');
		expect(templateFiltersFromSearchParams(params)).toEqual(filters);
	});

	it('omits defaults from the URL so a clean grid has no query string', () => {
		expect(templateFiltersToSearchParams(DEFAULT_TEMPLATE_FILTERS).toString()).toBe('');
	});

	it('falls back to the defaults for unknown slot and sort values', () => {
		const params = new URLSearchParams({ slots: '9-12', sort_by: 'usage' });
		const filters = templateFiltersFromSearchParams(params);
		expect(filters.slots).toBe('');
		expect(filters.sortBy).toBe('name');
	});

	it('drops blank entries from the tags list', () => {
		const filters = templateFiltersFromSearchParams(new URLSearchParams({ tags: 'portrait, ,lighting' }));
		expect(filters.tags).toEqual(['portrait', 'lighting']);
	});
});

describe('applyTemplateFilters', () => {
	const one = template({ id: 'one', name: 'Solo slot', segments: [slot('Subject')], tags: ['quick'] });
	const three = template({ id: 'three', name: 'Portrait base' });
	const five = template({
		id: 'five',
		name: 'Cinematic rig',
		description: 'Wide coverage',
		segments: [slot('A'), slot('B'), slot('C'), slot('D'), slot('E')],
		tags: ['film'],
		updated_at: '2026-09-20T00:00:00.000Z'
	});
	const all = [three, one, five];

	it('buckets by slot count', () => {
		expect(applyTemplateFilters(all, { ...DEFAULT_TEMPLATE_FILTERS, slots: '1' }).map((t) => t.id)).toEqual(['one']);
		expect(applyTemplateFilters(all, { ...DEFAULT_TEMPLATE_FILTERS, slots: '2-3' }).map((t) => t.id)).toEqual([
			'three'
		]);
		expect(applyTemplateFilters(all, { ...DEFAULT_TEMPLATE_FILTERS, slots: '4+' }).map((t) => t.id)).toEqual(['five']);
	});

	it('matches the search against name, description, tags and slot names', () => {
		const byName = applyTemplateFilters(all, { ...DEFAULT_TEMPLATE_FILTERS, q: 'cinematic' });
		expect(byName.map((t) => t.id)).toEqual(['five']);
		const byDescription = applyTemplateFilters(all, { ...DEFAULT_TEMPLATE_FILTERS, q: 'wide coverage' });
		expect(byDescription.map((t) => t.id)).toEqual(['five']);
		const byTag = applyTemplateFilters(all, { ...DEFAULT_TEMPLATE_FILTERS, q: 'quick' });
		expect(byTag.map((t) => t.id)).toEqual(['one']);
		const bySlotName = applyTemplateFilters(all, { ...DEFAULT_TEMPLATE_FILTERS, q: 'camera' });
		expect(bySlotName.map((t) => t.id)).toEqual(['three']);
	});

	it('keeps a template that carries any of the selected tags', () => {
		const rows = applyTemplateFilters(all, { ...DEFAULT_TEMPLATE_FILTERS, tags: ['film', 'quick'] });
		expect(rows.map((t) => t.id).sort()).toEqual(['five', 'one']);
	});

	it('sorts by name and by most recently updated', () => {
		expect(applyTemplateFilters(all, DEFAULT_TEMPLATE_FILTERS).map((t) => t.name)).toEqual([
			'Cinematic rig',
			'Portrait base',
			'Solo slot'
		]);
		expect(applyTemplateFilters(all, { ...DEFAULT_TEMPLATE_FILTERS, sortBy: 'created' })[0].id).toBe('five');
	});

	it('does not mutate the list it was given', () => {
		const source = [three, one, five];
		applyTemplateFilters(source, DEFAULT_TEMPLATE_FILTERS);
		expect(source.map((t) => t.id)).toEqual(['three', 'one', 'five']);
	});
});

describe('template filter chips', () => {
	it('emits one chip per tag plus a slot chip, and clears them individually', () => {
		const filters: TemplateFilters = { q: 'x', tags: ['portrait', 'film'], slots: '4+', sortBy: 'name' };
		expect(templateFilterChips(filters)).toEqual([
			{ key: 'slots', label: '4+ slots' },
			{ key: 'tag:portrait', label: '#portrait' },
			{ key: 'tag:film', label: '#film' }
		]);
		expect(clearTemplateFilterChip(filters, 'tag:portrait').tags).toEqual(['film']);
		expect(clearTemplateFilterChip(filters, 'slots').slots).toBe('');
		expect(clearTemplateFilterChip(filters, 'nothing')).toBe(filters);
	});

	it('singularises the slot chip for a one-slot filter', () => {
		expect(templateFilterChips({ ...DEFAULT_TEMPLATE_FILTERS, slots: '1' })[0].label).toBe('1 slot');
	});

	it('counts tags as one active filter and keeps query and sort when clearing all', () => {
		const filters: TemplateFilters = { q: 'keep me', tags: ['a', 'b'], slots: '1', sortBy: 'created' };
		expect(templateFilterActiveCount(filters)).toBe(2);
		expect(clearAllTemplateFilters(filters)).toEqual({ q: 'keep me', tags: [], slots: '', sortBy: 'created' });
		expect(templateFilterActiveCount(DEFAULT_TEMPLATE_FILTERS)).toBe(0);
	});
});

describe('slot labels and tag vocabulary', () => {
	it('names an unnamed slot by its position', () => {
		expect(templateSlotLabels({ segments: [slot('Subject'), slot(null), slot('  ')] })).toEqual([
			'Subject',
			'Slot 2',
			'Slot 3'
		]);
	});

	it('counts each tag and orders the vocabulary by count then name', () => {
		expect(templateTagVocabulary([template(), template({ id: 'b', tags: ['lighting', 'film'] })])).toEqual([
			{ tag: 'lighting', count: 2 },
			{ tag: 'film', count: 1 },
			{ tag: 'portrait', count: 1 }
		]);
	});
});
