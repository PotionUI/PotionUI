import { describe, expect, it } from 'vitest';
import type { PresetStyle } from '$lib/types/api';
import { categoryOptions, filterStyles } from './styleFilter';

function style(id: string, partial: Partial<PresetStyle> = {}): PresetStyle {
	return {
		id,
		name: `Style ${id}`,
		category: 'Anime',
		prepend: `${id} prepend`,
		append: `${id} append`,
		example_prompt: `${id} example`,
		...partial
	};
}

describe('categoryOptions', () => {
	it('leads with All at the total count, then categories in first-appearance order', () => {
		const styles = [
			style('a', { category: 'Anime' }),
			style('b', { category: 'Realistic' }),
			style('c', { category: 'Anime' })
		];
		expect(categoryOptions(styles)).toEqual([
			{ id: 'all', label: 'All', count: 3 },
			{ id: 'Anime', label: 'Anime', count: 2 },
			{ id: 'Realistic', label: 'Realistic', count: 1 }
		]);
	});

	it('falls back missing categories to General, grouped together', () => {
		const styles = [style('a', { category: '' }), style('b', { category: undefined as unknown as string })];
		expect(categoryOptions(styles)).toEqual([
			{ id: 'all', label: 'All', count: 2 },
			{ id: 'General', label: 'General', count: 2 }
		]);
	});

	it('is just the All chip at zero count for an empty list', () => {
		expect(categoryOptions([])).toEqual([{ id: 'all', label: 'All', count: 0 }]);
	});
});

describe('filterStyles', () => {
	const styles = [
		style('retro', { name: 'Retro 90s', category: 'Anime', description: 'VHS-warped cel animation' }),
		style('noir', { name: 'Film Noir', category: 'Realistic', description: 'High-contrast black and white' }),
		style('cyber', { name: 'Cyberpunk', category: 'Anime', description: 'Neon-drenched megacity' })
	];

	it('returns everything for category all and no text', () => {
		expect(filterStyles(styles, 'all', '')).toHaveLength(3);
	});

	it('filters by exact category', () => {
		expect(filterStyles(styles, 'Anime', '').map((s) => s.id)).toEqual(['retro', 'cyber']);
	});

	it('matches text against the name, case-insensitively', () => {
		expect(filterStyles(styles, 'all', 'retro').map((s) => s.id)).toEqual(['retro']);
		expect(filterStyles(styles, 'all', 'RETRO').map((s) => s.id)).toEqual(['retro']);
	});

	it('matches text against the description', () => {
		expect(filterStyles(styles, 'all', 'neon').map((s) => s.id)).toEqual(['cyber']);
	});

	it('matches text against the category', () => {
		expect(filterStyles(styles, 'all', 'realistic').map((s) => s.id)).toEqual(['noir']);
	});

	it('ANDs category and text together', () => {
		expect(filterStyles(styles, 'Anime', 'neon').map((s) => s.id)).toEqual(['cyber']);
		expect(filterStyles(styles, 'Realistic', 'neon')).toEqual([]);
	});

	it('trims surrounding whitespace on the text filter', () => {
		expect(filterStyles(styles, 'all', '  retro  ').map((s) => s.id)).toEqual(['retro']);
	});
});
