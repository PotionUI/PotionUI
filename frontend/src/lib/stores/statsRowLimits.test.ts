import { describe, it, expect } from 'vitest';
import { get } from 'svelte/store';
import { statsRowLimits, STATS_ROW_LIMIT_OPTIONS } from './statsRowLimits';

// vitest runs with `environment: 'node'` (vite.config.ts), so `$app/environment`'s
// `browser` is false and the localStorage read/write paths are skipped here --
// these tests exercise the in-memory store contract, which `setLimit` updates
// unconditionally regardless of persistence.

describe('statsRowLimits', () => {
	it('starts with sane per-section defaults', () => {
		const state = get(statsRowLimits);
		expect(state.presets).toBe(8);
		expect(state.models).toBe(8);
		expect(state.storage).toBe(30); // the "disk usage" section
		expect(state.presetTiming).toBe(10);
		expect(state.presetResources).toBe(10);
	});

	it('setLimit updates only the targeted section', () => {
		statsRowLimits.setLimit('presets', 20);

		const state = get(statsRowLimits);
		expect(state.presets).toBe(20);
		expect(state.models).toBe(8); // untouched
	});

	it('setLimit on the storage section does not affect presetTiming', () => {
		statsRowLimits.setLimit('storage', 100);

		const state = get(statsRowLimits);
		expect(state.storage).toBe(100);
		expect(state.presetTiming).toBe(10);
	});

	it('exposes a fixed, ascending option list', () => {
		const sorted = [...STATS_ROW_LIMIT_OPTIONS].sort((a, b) => a - b);
		expect(STATS_ROW_LIMIT_OPTIONS).toEqual(sorted);
	});
});
