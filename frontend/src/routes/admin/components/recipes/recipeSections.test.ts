import { describe, it, expect } from 'vitest';
import type { RecipeSummary } from '$lib/services/api/recipes';
import {
	RECIPE_SECTIONS,
	recipeCategoryLabel,
	recipeSectionCounts,
	recipeSectionFromParam,
	recipesInSection
} from './recipeSections';

function recipe(id: string, category: string): RecipeSummary {
	return {
		id,
		name: id,
		summary: '',
		description: '',
		engine: 'native',
		category,
		artifact_count: 0,
		step_count: 0,
		presets: [],
		source: 'marketplace',
		plugin_id: null,
		total_download_bytes: null,
		last_completed_at: null
	};
}

describe('RECIPE_SECTIONS', () => {
	it('starts with the all-recipes catch-all followed by the five closed categories', () => {
		expect(RECIPE_SECTIONS.map((entry) => entry.id)).toEqual([
			'all',
			'image',
			'video',
			'audio',
			'3d',
			'utility'
		]);
	});
});

describe('recipeCategoryLabel', () => {
	it('titlecases a category and special-cases 3d', () => {
		expect(recipeCategoryLabel('image')).toBe('Image');
		expect(recipeCategoryLabel('3d')).toBe('3D');
	});

	it('falls back to the raw value for an unrecognized category', () => {
		expect(recipeCategoryLabel('mystery')).toBe('mystery');
	});
});

describe('recipeSectionFromParam', () => {
	it('accepts any of the closed section values', () => {
		expect(recipeSectionFromParam('video')).toBe('video');
		expect(recipeSectionFromParam('all')).toBe('all');
	});

	it('falls back to all for a missing or unknown value', () => {
		expect(recipeSectionFromParam(null)).toBe('all');
		expect(recipeSectionFromParam('not-a-category')).toBe('all');
	});
});

describe('recipesInSection', () => {
	const recipes = [recipe('a', 'image'), recipe('b', 'video'), recipe('c', 'image')];

	it('keeps everything for the all section', () => {
		expect(recipesInSection(recipes, 'all').map((r) => r.id)).toEqual(['a', 'b', 'c']);
	});

	it('keeps only the matching category otherwise', () => {
		expect(recipesInSection(recipes, 'image').map((r) => r.id)).toEqual(['a', 'c']);
		expect(recipesInSection(recipes, 'audio')).toEqual([]);
	});
});

describe('recipeSectionCounts', () => {
	it('counts every recipe under all plus a count per category', () => {
		const recipes = [recipe('a', 'image'), recipe('b', 'video'), recipe('c', 'image')];
		expect(recipeSectionCounts(recipes)).toEqual({ all: 3, image: 2, video: 1 });
	});

	it('returns just the all count for an empty catalog', () => {
		expect(recipeSectionCounts([])).toEqual({ all: 0 });
	});
});
