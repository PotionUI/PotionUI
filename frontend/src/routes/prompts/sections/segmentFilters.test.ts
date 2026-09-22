import { describe, expect, it } from 'vitest';
import type { SavedSegment, SegmentCategory } from '$lib/types/segments';
import {
	DEFAULT_SEGMENT_FILTERS,
	applySegmentFilters,
	clearAllSegmentFilters,
	clearSegmentFilterChip,
	segmentCountsByCategory,
	segmentFilterActiveCount,
	segmentFilterChips,
	segmentFiltersFromSearchParams,
	segmentFiltersToSearchParams,
	segmentTagVocabulary,
	type SegmentFilters
} from './segmentFilters';

const categories: SegmentCategory[] = [
	{ id: 'cat-light', name: 'Lighting', description: '', color: '#F59E0B' },
	{ id: 'cat-cam', name: 'Camera', description: '', color: '#3B82F6' }
];

function segment(overrides: Partial<SavedSegment> = {}): SavedSegment {
	return {
		id: 'seg-1',
		name: 'Golden hour',
		category_id: 'cat-light',
		type: 'content',
		content: 'warm golden hour sunlight, long soft shadows',
		chips: {},
		enabled: true,
		tags: ['portrait', 'outdoor'],
		description: 'Late afternoon key light',
		created_at: '2026-09-01T00:00:00.000Z',
		updated_at: '2026-09-10T00:00:00.000Z',
		...overrides
	};
}

const segments: SavedSegment[] = [
	segment(),
	segment({
		id: 'seg-2',
		name: 'Low angle push',
		category_id: 'cat-cam',
		content: 'low angle, slow push in, 35mm',
		tags: ['motion'],
		description: null,
		updated_at: '2026-09-20T00:00:00.000Z'
	}),
	segment({
		id: 'seg-3',
		name: 'Break',
		category_id: 'cat-cam',
		type: 'break',
		content: '',
		tags: [],
		enabled: false,
		description: null,
		updated_at: '2026-08-01T00:00:00.000Z'
	})
];

describe('segment filters URL round-trip', () => {
	it('parses every param and serialises it back identically', () => {
		const params = new URLSearchParams(
			'q=gold&category=cat-light&type=break&enabled=off&tags=portrait,motion&sort_by=created'
		);
		const filters = segmentFiltersFromSearchParams(params);
		expect(filters).toEqual<SegmentFilters>({
			q: 'gold',
			category: 'cat-light',
			type: 'break',
			enabled: 'off',
			tags: ['portrait', 'motion'],
			sortBy: 'created'
		});
		expect(segmentFiltersFromSearchParams(segmentFiltersToSearchParams(filters))).toEqual(filters);
	});

	it('omits defaults from the URL', () => {
		expect(segmentFiltersToSearchParams(DEFAULT_SEGMENT_FILTERS).toString()).toBe('');
		expect(segmentFiltersToSearchParams({ ...DEFAULT_SEGMENT_FILTERS, sortBy: 'name' }).has('sort_by')).toBe(false);
	});

	it('falls back to defaults for unknown values', () => {
		const filters = segmentFiltersFromSearchParams(new URLSearchParams('type=video&enabled=maybe&sort_by=usage'));
		expect(filters.type).toBe('');
		expect(filters.enabled).toBe('');
		expect(filters.sortBy).toBe('name');
	});
});

describe('applySegmentFilters', () => {
	it('searches name, content, description, tags and category name', () => {
		const byName = applySegmentFilters(segments, categories, { ...DEFAULT_SEGMENT_FILTERS, q: 'golden' });
		expect(byName.map((s) => s.id)).toEqual(['seg-1']);
		const byContent = applySegmentFilters(segments, categories, { ...DEFAULT_SEGMENT_FILTERS, q: '35mm' });
		expect(byContent.map((s) => s.id)).toEqual(['seg-2']);
		const byDescription = applySegmentFilters(segments, categories, { ...DEFAULT_SEGMENT_FILTERS, q: 'key light' });
		expect(byDescription.map((s) => s.id)).toEqual(['seg-1']);
		const byTag = applySegmentFilters(segments, categories, { ...DEFAULT_SEGMENT_FILTERS, q: 'motion' });
		expect(byTag.map((s) => s.id)).toEqual(['seg-2']);
		const byCategory = applySegmentFilters(segments, categories, { ...DEFAULT_SEGMENT_FILTERS, q: 'camera' });
		expect(byCategory.map((s) => s.id).sort()).toEqual(['seg-2', 'seg-3']);
	});

	it('narrows by category, type, enabled and any-of tags', () => {
		expect(
			applySegmentFilters(segments, categories, { ...DEFAULT_SEGMENT_FILTERS, category: 'cat-cam' }).map((s) => s.id)
		).toEqual(['seg-3', 'seg-2']);
		expect(applySegmentFilters(segments, categories, { ...DEFAULT_SEGMENT_FILTERS, type: 'break' }).map((s) => s.id)).toEqual([
			'seg-3'
		]);
		expect(applySegmentFilters(segments, categories, { ...DEFAULT_SEGMENT_FILTERS, enabled: 'off' }).map((s) => s.id)).toEqual([
			'seg-3'
		]);
		expect(applySegmentFilters(segments, categories, { ...DEFAULT_SEGMENT_FILTERS, enabled: 'on' }).length).toBe(2);
		expect(
			applySegmentFilters(segments, categories, { ...DEFAULT_SEGMENT_FILTERS, tags: ['outdoor', 'motion'] }).map((s) => s.id)
		).toEqual(['seg-1', 'seg-2']);
	});

	it('sorts by name or by most recently updated', () => {
		expect(applySegmentFilters(segments, categories, DEFAULT_SEGMENT_FILTERS).map((s) => s.id)).toEqual([
			'seg-3',
			'seg-1',
			'seg-2'
		]);
		expect(applySegmentFilters(segments, categories, { ...DEFAULT_SEGMENT_FILTERS, sortBy: 'created' }).map((s) => s.id)).toEqual([
			'seg-2',
			'seg-1',
			'seg-3'
		]);
	});
});

describe('segment filter chips', () => {
	const filters: SegmentFilters = {
		...DEFAULT_SEGMENT_FILTERS,
		q: 'keep',
		category: 'cat-light',
		type: 'content',
		enabled: 'on',
		tags: ['portrait'],
		sortBy: 'created'
	};

	it('labels the category chip with the category name and counts active filters', () => {
		expect(segmentFilterChips(filters, categories)).toEqual([
			{ key: 'category', label: 'category = Lighting' },
			{ key: 'type', label: 'content' },
			{ key: 'enabled', label: 'enabled' },
			{ key: 'tag:portrait', label: '#portrait' }
		]);
		expect(segmentFilterActiveCount(filters)).toBe(4);
		expect(segmentFilterActiveCount(DEFAULT_SEGMENT_FILTERS)).toBe(0);
	});

	it('clears one chip at a time and keeps query and sort on clear all', () => {
		expect(clearSegmentFilterChip(filters, 'category').category).toBe('');
		expect(clearSegmentFilterChip(filters, 'type').type).toBe('');
		expect(clearSegmentFilterChip(filters, 'enabled').enabled).toBe('');
		expect(clearSegmentFilterChip(filters, 'tag:portrait').tags).toEqual([]);
		expect(clearSegmentFilterChip(filters, 'unknown')).toBe(filters);
		expect(clearAllSegmentFilters(filters)).toEqual({ ...DEFAULT_SEGMENT_FILTERS, q: 'keep', sortBy: 'created' });
	});
});

describe('segment vocabularies', () => {
	it('collects tags with counts and segments per category', () => {
		expect(segmentTagVocabulary(segments)).toEqual([
			{ tag: 'motion', count: 1 },
			{ tag: 'outdoor', count: 1 },
			{ tag: 'portrait', count: 1 }
		]);
		expect(Array.from(segmentCountsByCategory(segments))).toEqual([
			['cat-light', 1],
			['cat-cam', 2]
		]);
	});
});
