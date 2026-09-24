import { describe, it, expect } from 'vitest';
import type { RecipeSummary } from '$lib/services/api/recipes';
import {
	DEFAULT_RECIPE_FILTERS,
	applyRecipeFilters,
	clearAllRecipeFilters,
	clearRecipeFilterChip,
	recipeEngineVocabulary,
	recipeFilterActiveCount,
	recipeFilterChips,
	recipeFiltersFromSearchParams,
	recipeFiltersToSearchParams,
	recipeSourceVocabulary,
	type RecipeFilters
} from './recipeFilters';

function recipe(overrides: Partial<RecipeSummary> = {}): RecipeSummary {
	return {
		id: 'sdxl-starter',
		name: 'SDXL Starter',
		summary: 'Get a working SDXL setup',
		description: 'Fetches the checkpoint and validates txt2img.',
		engine: 'native',
		category: 'image',
		artifact_count: 1,
		step_count: 5,
		presets: [{ id: 'sdxl/base', name: 'SDXL', cover_url: null, installed: false }],
		source: 'marketplace',
		plugin_id: null,
		total_download_bytes: 1000,
		last_completed_at: null,
		...overrides
	};
}

describe('recipeFiltersFromSearchParams / recipeFiltersToSearchParams', () => {
	it('round-trips a fully-set filter state through the URL', () => {
		const filters: RecipeFilters = { q: 'sdxl', source: 'marketplace', engine: 'native', sortBy: 'category' };
		const params = recipeFiltersToSearchParams(filters);
		expect(params.get('q')).toBe('sdxl');
		expect(params.get('source')).toBe('marketplace');
		expect(params.get('engine')).toBe('native');
		expect(params.get('sort_by')).toBe('category');
		expect(recipeFiltersFromSearchParams(params)).toEqual(filters);
	});

	it('omits defaults from the URL so a clean list has no query string', () => {
		expect(recipeFiltersToSearchParams(DEFAULT_RECIPE_FILTERS).toString()).toBe('');
	});

	it('falls back to the default sort for an unknown sort_by value', () => {
		const filters = recipeFiltersFromSearchParams(new URLSearchParams({ sort_by: 'download_size' }));
		expect(filters.sortBy).toBe('name');
	});
});

describe('applyRecipeFilters', () => {
	const sdxl = recipe({ id: 'sdxl-starter', name: 'SDXL Starter', engine: 'native', source: 'marketplace' });
	const comfy = recipe({
		id: 'comfyui-detect',
		name: 'ComfyUI Detect',
		description: 'Detects a running ComfyUI server.',
		engine: 'comfyui',
		source: 'plugin',
		category: 'utility'
	});
	const local = recipe({ id: 'flux-local', name: 'Flux Local', engine: 'native', source: 'local', category: 'image' });
	const all = [comfy, sdxl, local];

	it('matches the search against name, id and description only', () => {
		expect(applyRecipeFilters(all, { ...DEFAULT_RECIPE_FILTERS, q: 'detect' }).map((r) => r.id)).toEqual([
			'comfyui-detect'
		]);
		expect(applyRecipeFilters(all, { ...DEFAULT_RECIPE_FILTERS, q: 'flux-local' }).map((r) => r.id)).toEqual([
			'flux-local'
		]);
		expect(applyRecipeFilters(all, { ...DEFAULT_RECIPE_FILTERS, q: 'running comfyui server' }).map((r) => r.id)).toEqual([
			'comfyui-detect'
		]);
	});

	it('filters by source', () => {
		expect(applyRecipeFilters(all, { ...DEFAULT_RECIPE_FILTERS, source: 'plugin' }).map((r) => r.id)).toEqual([
			'comfyui-detect'
		]);
	});

	it('filters by engine', () => {
		expect(applyRecipeFilters(all, { ...DEFAULT_RECIPE_FILTERS, engine: 'comfyui' }).map((r) => r.id)).toEqual([
			'comfyui-detect'
		]);
	});

	it('sorts by name and by category then name', () => {
		expect(applyRecipeFilters(all, DEFAULT_RECIPE_FILTERS).map((r) => r.id)).toEqual([
			'comfyui-detect',
			'flux-local',
			'sdxl-starter'
		]);
		expect(applyRecipeFilters(all, { ...DEFAULT_RECIPE_FILTERS, sortBy: 'category' }).map((r) => r.id)).toEqual([
			'flux-local',
			'sdxl-starter',
			'comfyui-detect'
		]);
	});

	it('does not mutate the list it was given', () => {
		const source = [comfy, sdxl, local];
		applyRecipeFilters(source, DEFAULT_RECIPE_FILTERS);
		expect(source.map((r) => r.id)).toEqual(['comfyui-detect', 'sdxl-starter', 'flux-local']);
	});
});

describe('recipe filter chips', () => {
	it('emits a chip per active source/engine filter and clears them individually', () => {
		const filters: RecipeFilters = { q: 'x', source: 'plugin', engine: 'comfyui', sortBy: 'name' };
		expect(recipeFilterChips(filters)).toEqual([
			{ key: 'source', label: 'source: plugin' },
			{ key: 'engine', label: 'engine: comfyui' }
		]);
		expect(clearRecipeFilterChip(filters, 'source').source).toBe('');
		expect(clearRecipeFilterChip(filters, 'nothing')).toBe(filters);
	});

	it('counts source and engine as active filters and keeps query and sort when clearing all', () => {
		const filters: RecipeFilters = { q: 'keep me', source: 'local', engine: 'native', sortBy: 'category' };
		expect(recipeFilterActiveCount(filters)).toBe(2);
		expect(clearAllRecipeFilters(filters)).toEqual({ q: 'keep me', source: '', engine: '', sortBy: 'category' });
		expect(recipeFilterActiveCount(DEFAULT_RECIPE_FILTERS)).toBe(0);
	});
});

describe('recipe source/engine vocabulary', () => {
	it('lists unique, sorted values', () => {
		const recipes = [
			recipe({ source: 'marketplace', engine: 'native' }),
			recipe({ source: 'plugin', engine: 'comfyui' }),
			recipe({ source: 'marketplace', engine: 'native' })
		];
		expect(recipeSourceVocabulary(recipes)).toEqual(['marketplace', 'plugin']);
		expect(recipeEngineVocabulary(recipes)).toEqual(['comfyui', 'native']);
	});
});
