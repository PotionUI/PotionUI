import { describe, it, expect } from 'vitest';
import { isSearching, splitHighlight, excerpt, groupFindResults, emptyFindResult } from './phrasebookFind';
import type { PhrasebookCategory, PhrasebookFindValueHit } from '$lib/types/api';

function category(id: string): PhrasebookCategory {
	return { id, name: id, path: id, description: '', is_active: true, created_at: '', updated_at: '' };
}

function hit(id: string): PhrasebookFindValueHit {
	return {
		id,
		category_id: 'cat',
		label: id,
		value: id,
		sort_order: 0,
		is_active: true,
		created_at: '',
		updated_at: '',
		category_path: 'cat',
		category_name: 'Cat',
		category_is_active: true
	};
}

describe('isSearching', () => {
	it('is false for empty or whitespace-only input', () => {
		expect(isSearching('')).toBe(false);
		expect(isSearching('   ')).toBe(false);
	});

	it('is true from a single non-blank character', () => {
		expect(isSearching(' d ')).toBe(true);
	});
});

describe('splitHighlight', () => {
	it('marks every case-insensitive occurrence and keeps the original casing', () => {
		expect(splitHighlight('Hot DOG and dogma', 'dog')).toEqual([
			{ text: 'Hot ', match: false },
			{ text: 'DOG', match: true },
			{ text: ' and ', match: false },
			{ text: 'dog', match: true },
			{ text: 'ma', match: false }
		]);
	});

	it('returns the whole text unmarked when nothing matches or the query is blank', () => {
		expect(splitHighlight('cat', 'dog')).toEqual([{ text: 'cat', match: false }]);
		expect(splitHighlight('cat', '  ')).toEqual([{ text: 'cat', match: false }]);
	});

	it('handles an empty text and a match at the very end', () => {
		expect(splitHighlight('', 'dog')).toEqual([]);
		expect(splitHighlight('hotdog', 'dog')).toEqual([
			{ text: 'hot', match: false },
			{ text: 'dog', match: true }
		]);
	});
});

describe('excerpt', () => {
	it('returns short text untouched apart from whitespace collapsing', () => {
		expect(excerpt('a   small\n dog', 'dog')).toBe('a small dog');
	});

	it('windows around the first match so the match survives truncation', () => {
		const text = `${'x'.repeat(100)} dog ${'y'.repeat(100)}`;
		const out = excerpt(text, 'dog', 40);
		expect(out).toContain('dog');
		expect(out.length).toBeLessThanOrEqual(42);
		expect(out.startsWith('…')).toBe(true);
		expect(out.endsWith('…')).toBe(true);
	});

	it('truncates from the start when the query is absent', () => {
		expect(excerpt('x'.repeat(100), 'dog', 10)).toBe(`${'x'.repeat(9)}…`);
	});
});

describe('groupFindResults', () => {
	it('caps each group but reports the server totals', () => {
		const groups = groupFindResults(
			{
				query: 'a',
				categories: [category('a'), category('b'), category('c')],
				values: [hit('v1')],
				total_categories: 3,
				total_values: 1
			},
			2
		);
		expect(groups.categories.items.map((c) => c.id)).toEqual(['a', 'b']);
		expect(groups.categories.count).toBe(3);
		expect(groups.values.items).toHaveLength(1);
		expect(groups.total).toBe(4);
	});
});

describe('emptyFindResult', () => {
	it('trims the query and carries zero totals', () => {
		expect(emptyFindResult(' dog ')).toEqual({
			query: 'dog',
			categories: [],
			values: [],
			total_categories: 0,
			total_values: 0
		});
	});
});
