import { describe, it, expect } from 'vitest';
import { normalizeCategories, toggleCategory, FAILURE_ALERT_CATEGORIES } from './failureAlerts';
import { computeDirtyGroups } from './settingsGroups';

describe('normalizeCategories', () => {
	it('keeps string entries of a list', () => {
		expect(normalizeCategories(['cuda_oom', 3, 'disk_full'])).toEqual(['cuda_oom', 'disk_full']);
	});

	it('parses a JSON string', () => {
		expect(normalizeCategories('["cuda_oom"]')).toEqual(['cuda_oom']);
	});

	it('treats missing or malformed values as no filter', () => {
		expect(normalizeCategories(undefined)).toEqual([]);
		expect(normalizeCategories('not json')).toEqual([]);
	});
});

describe('toggleCategory', () => {
	it('adds an unselected category', () => {
		expect(toggleCategory([], 'cuda_oom')).toEqual(['cuda_oom']);
	});

	it('removes a selected category', () => {
		expect(toggleCategory(['cuda_oom', 'disk_full'], 'cuda_oom')).toEqual(['disk_full']);
	});
});

describe('failure alert settings', () => {
	it('ride the Generation group save bar', () => {
		expect(
			computeDirtyGroups([
				'notify_admins_on_generation_failure',
				'notify_admins_on_generation_failure_categories'
			])
		).toEqual(new Set(['generation']));
	});

	it('list every backend error category once', () => {
		const values = FAILURE_ALERT_CATEGORIES.map((c) => c.value);
		expect(new Set(values).size).toBe(values.length);
		expect(values).toContain('unclassified');
	});
});
