import { describe, expect, it } from 'vitest';
import { matchesSearch, filterBySearch } from './searchFilter';

describe('matchesSearch', () => {
	it('matches case-insensitively', () => {
		expect(matchesSearch('Anime', 'ani')).toBe(true);
		expect(matchesSearch('Anime', 'ANI')).toBe(true);
	});

	it('rejects a non-matching substring', () => {
		expect(matchesSearch('Anime', 'photo')).toBe(false);
	});

	it('treats a blank or whitespace-only query as matching everything', () => {
		expect(matchesSearch('Anime', '')).toBe(true);
		expect(matchesSearch('Anime', '   ')).toBe(true);
	});

	it('trims the query before matching', () => {
		expect(matchesSearch('Anime', '  ani  ')).toBe(true);
	});
});

describe('filterBySearch', () => {
	const items = [{ name: 'Anime' }, { name: 'Photoreal' }, { name: 'Cartoon' }];

	it('filters by the label selector', () => {
		expect(filterBySearch(items, 'o', (i) => i.name)).toEqual([{ name: 'Photoreal' }, { name: 'Cartoon' }]);
	});

	it('returns everything for a blank query', () => {
		expect(filterBySearch(items, '', (i) => i.name)).toEqual(items);
	});
});
