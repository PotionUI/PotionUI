import { describe, it, expect } from 'vitest';
import { categoryLabel, durationFor, presetTitleFor, sortByFromSortState, sortStateFromSortBy } from './generationsColumns';

describe('durationFor', () => {
	it('formats the wall-clock gap between created and completed', () => {
		expect(
			durationFor({ status: 'completed', created_at: '2026-08-14T00:00:00Z', completed_at: '2026-08-14T00:00:05Z' })
		).toBe('5.0s');
	});

	it('reports "running" for an in-flight generation with no completed_at', () => {
		expect(durationFor({ status: 'running', created_at: '2026-08-14T00:00:00Z', completed_at: undefined })).toBe(
			'running'
		);
	});

	it('falls back to an em dash for a non-running row missing completed_at', () => {
		expect(durationFor({ status: 'pending', created_at: '2026-08-14T00:00:00Z', completed_at: undefined })).toBe(
			'—'
		);
	});

	it('falls back to an em dash when completed precedes created (bad data)', () => {
		expect(
			durationFor({ status: 'completed', created_at: '2026-08-14T00:00:05Z', completed_at: '2026-08-14T00:00:00Z' })
		).toBe('—');
	});
});

describe('presetTitleFor', () => {
	it('prefers the preset name, then the mode, then a fallback', () => {
		expect(presetTitleFor({ preset_name: 'SDXL', mode: 't2i' })).toBe('SDXL');
		expect(presetTitleFor({ preset_name: undefined, mode: 't2i' })).toBe('t2i');
		expect(presetTitleFor({ preset_name: undefined, mode: undefined })).toBe('Untitled generation');
	});
});

describe('categoryLabel', () => {
	it('maps a failure category code to its friendly label', () => {
		expect(categoryLabel('cuda_oom')).toBe('GPU out of memory');
		expect(categoryLabel('disk_full')).toBe('Disk full');
	});

	it('falls back to the raw code for an unrecognized category', () => {
		expect(categoryLabel('some_new_category')).toBe('some_new_category');
	});

	it('falls back to an em dash for a generation with no failure category', () => {
		expect(categoryLabel(null)).toBe('—');
		expect(categoryLabel(undefined)).toBe('—');
	});
});

describe('sort <-> filters bridge', () => {
	it('round-trips created_desc/created_asc through the table SortState', () => {
		expect(sortStateFromSortBy('created_desc')).toEqual({ key: 'created', dir: 'desc' });
		expect(sortStateFromSortBy('created_asc')).toEqual({ key: 'created', dir: 'asc' });
		expect(sortByFromSortState({ key: 'created', dir: 'desc' })).toBe('created_desc');
		expect(sortByFromSortState({ key: 'created', dir: 'asc' })).toBe('created_asc');
	});

	it('falls back to created_desc when the column is cleared or is not the created column', () => {
		expect(sortByFromSortState(null)).toBe('created_desc');
		expect(sortByFromSortState({ key: 'preset', dir: 'asc' })).toBe('created_desc');
	});
});
