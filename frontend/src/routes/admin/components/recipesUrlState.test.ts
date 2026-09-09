import { describe, it, expect } from 'vitest';
import { readRecipesUrlState, writeRecipesUrlState } from './recipesUrlState';

describe('readRecipesUrlState', () => {
	it('reads the selected recipe id', () => {
		const params = new URLSearchParams('tab=recipes&recipe=comfyui-detect');
		expect(readRecipesUrlState(params)).toEqual({ recipeId: 'comfyui-detect' });
	});

	it('reports nothing selected when the param is absent', () => {
		expect(readRecipesUrlState(new URLSearchParams('tab=recipes'))).toEqual({ recipeId: null });
	});
});

describe('writeRecipesUrlState', () => {
	it('writes the selected recipe without disturbing other params', () => {
		const next = writeRecipesUrlState(new URL('http://x/admin?tab=recipes&view=a'), {
			recipeId: 'sdxl-starter'
		});
		expect(next.searchParams.get('recipe')).toBe('sdxl-starter');
		expect(next.searchParams.get('tab')).toBe('recipes');
		expect(next.searchParams.get('view')).toBe('a');
	});

	it('removes the param when nothing is selected', () => {
		const next = writeRecipesUrlState(new URL('http://x/admin?tab=recipes&recipe=sdxl'), {
			recipeId: null
		});
		expect(next.searchParams.has('recipe')).toBe(false);
		expect(next.search).toBe('?tab=recipes');
	});

	it('does not mutate the url it was given', () => {
		const url = new URL('http://x/admin?tab=recipes');
		writeRecipesUrlState(url, { recipeId: 'sdxl' });
		expect(url.searchParams.has('recipe')).toBe(false);
	});
});
