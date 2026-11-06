import { describe, it, expect } from 'vitest';
import type { RecipeSummary } from '$lib/services/api/recipes';
import { isModesRecipe, modesBadge, modesDiskLine, modesToAdd, partitionRecipes } from './recipeModesDisplay';

const base = {
	id: 'r',
	name: 'R',
	summary: '',
	description: '',
	engine: 'native',
	category: 'video',
	artifact_count: 0,
	total_download_bytes: null,
	last_completed_at: null,
	source: 'derived',
	plugin_id: null,
	step_count: 2,
	presets: []
} as RecipeSummary;

describe('recipe modes display', () => {
	it('words the badge as ready of total, and success once every mode is ready', () => {
		expect(modesBadge({ modes_total: 12, modes_ready: 8 })).toEqual({ label: '8 of 12 modes ready', variant: 'warning' });
		expect(modesBadge({ modes_total: 5, modes_ready: 5 })).toEqual({ label: 'All 5 modes ready', variant: 'success' });
	});

	it('writes the disk line with the real size and the modes still to add', () => {
		const recipe = { modes_total: 12, modes_ready: 8, installed_bytes: 35.6 * 1024 ** 3 };
		expect(modesDiskLine(recipe)).toBe('35.6 GB on disk · 4 to add');
		expect(modesToAdd(recipe)).toBe(4);
		expect(modesDiskLine({ modes_total: 5, modes_ready: 5, installed_bytes: 41 * 1024 ** 3 })).toBe('41.0 GB on disk');
	});

	it('splits derived mode recipes from starters', () => {
		const modes = { ...base, id: 'modes-x', modes_total: 3, modes_ready: 1 };
		const starter = { ...base, id: 'starter', modes_total: null };
		expect(isModesRecipe(modes)).toBe(true);
		expect(isModesRecipe(starter)).toBe(false);
		const parts = partitionRecipes([starter, modes]);
		expect(parts.modes.map((r) => r.id)).toEqual(['modes-x']);
		expect(parts.starters.map((r) => r.id)).toEqual(['starter']);
	});
});
