import { describe, it, expect } from 'vitest';
import {
	addTag,
	canAddCustom,
	canAddMore,
	clearCategory,
	emptyTagsValue,
	filterCategoryTags,
	hasExactMatch,
	joinTagsValue,
	normalizeTagsValue,
	removeLastTag,
	removeTag,
	toggleTag,
	totalTagCount,
	type TagsCategory
} from './tagsValue';

const categories: TagsCategory[] = [
	{ key: 'colour', label: 'Colour', multi: false, allow_custom: true, tags: ['red', 'blue', 'green'] },
	{ key: 'shape', label: 'Shape', multi: true, allow_custom: true, tags: ['round', 'square'] },
	{ key: 'size', label: 'Size', multi: true, allow_custom: false, tags: ['small', 'large'] }
];

describe('emptyTagsValue', () => {
	it('creates an empty array per declared category', () => {
		expect(emptyTagsValue(categories)).toEqual({ colour: [], shape: [], size: [] });
	});
});

describe('normalizeTagsValue', () => {
	it('returns empty map for undefined', () => {
		expect(normalizeTagsValue(undefined, categories)).toEqual({ colour: [], shape: [], size: [] });
	});

	it('returns empty map for {}', () => {
		expect(normalizeTagsValue({}, categories)).toEqual({ colour: [], shape: [], size: [] });
	});

	it('keeps only declared category keys and dedupes/trims within a category', () => {
		const result = normalizeTagsValue(
			{ colour: [' red ', 'red', 'blue'], ghost: ['nope'] },
			categories
		);
		expect(result).toEqual({ colour: ['red', 'blue'], shape: [], size: [] });
	});

	it('splits a legacy string on the separator and maps each token by category tag match', () => {
		const result = normalizeTagsValue('red, round', categories, ', ');
		expect(result).toEqual({ colour: ['red'], shape: ['round'], size: [] });
	});

	it('matches legacy tokens against a category tag case-insensitively', () => {
		const result = normalizeTagsValue('RED, Round', categories, ', ');
		expect(result).toEqual({ colour: ['RED'], shape: ['Round'], size: [] });
	});

	it('routes an unmatched legacy token into the last category', () => {
		const result = normalizeTagsValue('mystery', categories, ', ');
		expect(result).toEqual({ colour: [], shape: [], size: ['mystery'] });
	});

	it('keeps only the first match for a single (multi: false) category and overflows later matches to the last category', () => {
		const result = normalizeTagsValue('red, blue, round', categories, ', ');
		expect(result).toEqual({ colour: ['red'], shape: ['round'], size: ['blue'] });
	});

	it('respects a custom separator when splitting a legacy string', () => {
		const result = normalizeTagsValue('red|round', categories, '|');
		expect(result).toEqual({ colour: ['red'], shape: ['round'], size: [] });
	});
});

describe('joinTagsValue', () => {
	it('joins tags in category order and tag order with the separator', () => {
		const value = { colour: ['red'], shape: ['round', 'square'], size: ['small'] };
		expect(joinTagsValue(value, categories, ', ')).toBe('red, round, square, small');
	});

	it('honors a custom separator', () => {
		const value = { colour: ['red'], shape: ['round'], size: [] };
		expect(joinTagsValue(value, categories, ' | ')).toBe('red | round');
	});
});

describe('addTag', () => {
	it('adds a trimmed tag to a multi category', () => {
		const value = emptyTagsValue(categories);
		expect(addTag(value, 'shape', '  round  ', categories)).toEqual({
			colour: [],
			shape: ['round'],
			size: []
		});
	});

	it('is a no-op for a blank tag', () => {
		const value = emptyTagsValue(categories);
		expect(addTag(value, 'shape', '   ', categories)).toBe(value);
	});

	it('drops a duplicate within the same category', () => {
		const value = { colour: [], shape: ['round'], size: [] };
		expect(addTag(value, 'shape', 'round', categories)).toBe(value);
	});

	it('replaces the existing value on a multi: false category', () => {
		const value = { colour: ['red'], shape: [], size: [] };
		expect(addTag(value, 'colour', 'blue', categories)).toEqual({ colour: ['blue'], shape: [], size: [] });
	});

	it('refuses to grow past max_tags', () => {
		const value = { colour: [], shape: ['round'], size: [] };
		expect(addTag(value, 'shape', 'square', categories, 1)).toBe(value);
	});

	it('still allows a multi: false replace at max_tags since the count does not grow', () => {
		const value = { colour: ['red'], shape: [], size: [] };
		expect(addTag(value, 'colour', 'blue', categories, 1)).toEqual({ colour: ['blue'], shape: [], size: [] });
	});
});

describe('removeTag / removeLastTag / clearCategory', () => {
	it('removeTag drops the given tag', () => {
		const value = { colour: [], shape: ['round', 'square'], size: [] };
		expect(removeTag(value, 'shape', 'round')).toEqual({ colour: [], shape: ['square'], size: [] });
	});

	it('removeLastTag drops the last tag in a category', () => {
		const value = { colour: [], shape: ['round', 'square'], size: [] };
		expect(removeLastTag(value, 'shape')).toEqual({ colour: [], shape: ['round'], size: [] });
	});

	it('removeLastTag is a no-op on an empty category', () => {
		const value = emptyTagsValue(categories);
		expect(removeLastTag(value, 'shape')).toBe(value);
	});

	it('clearCategory empties a filled category', () => {
		const value = { colour: ['red'], shape: [], size: [] };
		expect(clearCategory(value, 'colour')).toEqual({ colour: [], shape: [], size: [] });
	});
});

describe('toggleTag', () => {
	it('adds when absent', () => {
		const value = emptyTagsValue(categories);
		expect(toggleTag(value, 'shape', 'round', categories)).toEqual({ colour: [], shape: ['round'], size: [] });
	});

	it('removes when present', () => {
		const value = { colour: [], shape: ['round'], size: [] };
		expect(toggleTag(value, 'shape', 'round', categories)).toEqual({ colour: [], shape: [], size: [] });
	});
});

describe('totalTagCount / canAddMore', () => {
	it('sums tags across every category', () => {
		const value = { colour: ['red'], shape: ['round', 'square'], size: [] };
		expect(totalTagCount(value)).toBe(3);
	});

	it('allows more when under max_tags', () => {
		const value = { colour: [], shape: ['round'], size: [] };
		expect(canAddMore(value, categories[1], 2)).toBe(true);
	});

	it('blocks more when at max_tags for a multi category', () => {
		const value = { colour: [], shape: ['round'], size: [] };
		expect(canAddMore(value, categories[1], 1)).toBe(false);
	});

	it('always allows a multi: false replace even at max_tags', () => {
		const value = { colour: ['red'], shape: [], size: [] };
		expect(canAddMore(value, categories[0], 1)).toBe(true);
	});
});

describe('filterCategoryTags / hasExactMatch', () => {
	it('filters case-insensitively by substring', () => {
		expect(filterCategoryTags(categories[0], 'RE')).toEqual(['red', 'green']);
	});

	it('returns every tag for a blank query', () => {
		expect(filterCategoryTags(categories[0], '  ')).toEqual(['red', 'blue', 'green']);
	});

	it('detects an exact match case-insensitively', () => {
		expect(hasExactMatch(categories[0], 'RED')).toBe(true);
		expect(hasExactMatch(categories[0], 're')).toBe(false);
	});
});

describe('canAddCustom', () => {
	it('uses the category flag alone, ignoring a stricter field-level flag', () => {
		expect(canAddCustom(false, categories[0])).toBe(true);
	});

	it('uses the category flag alone, ignoring a looser field-level flag', () => {
		expect(canAddCustom(true, categories[2])).toBe(false);
	});

	it('falls back to the field-level flag when the category omits allow_custom', () => {
		const { allow_custom, ...rest } = categories[0];
		const category = rest as TagsCategory;
		expect(canAddCustom(false, category)).toBe(false);
		expect(canAddCustom(true, category)).toBe(true);
	});
});
