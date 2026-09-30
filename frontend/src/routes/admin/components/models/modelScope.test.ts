import { describe, it, expect } from 'vitest';
import {
	addScopePreset,
	removeScopePreset,
	scopeDraftIsDirty,
	scopeOptions,
	scopePayload,
	scopeRows
} from './modelScope';

const scope = {
	presets: [
		{ id: 'a', title: 'Alpha', engine: 'cloud', driver: 'cloud.x', missing: false, compatible: true },
		{ id: 'gone', title: null, engine: null, driver: null, missing: true, compatible: false },
		{ id: 'old', title: 'Old', engine: 'cloud', driver: 'cloud.y', missing: false, compatible: false }
	],
	candidates: [
		{ id: 'a', title: 'Alpha' },
		{ id: 'b', title: 'Beta' }
	]
};

describe('modelScope', () => {
	it('detects dirtiness regardless of order', () => {
		expect(scopeDraftIsDirty(['a', 'b'], ['b', 'a'])).toBe(false);
		expect(scopeDraftIsDirty(['a'], ['a', 'b'])).toBe(true);
		expect(scopeDraftIsDirty(['a', 'c'], ['a', 'b'])).toBe(true);
	});

	it('adds without duplicating and removes', () => {
		expect(addScopePreset(['a'], 'a')).toEqual(['a']);
		expect(addScopePreset(['a'], 'b')).toEqual(['a', 'b']);
		expect(removeScopePreset(['a', 'b'], 'a')).toEqual(['b']);
	});

	it('resolves rows from presets and candidates and flags missing and incompatible', () => {
		const rows = scopeRows(['a', 'gone', 'old', 'b'], scope);
		expect(rows.map((r) => [r.id, r.title, r.missing, r.compatible])).toEqual([
			['a', 'Alpha', false, true],
			['gone', 'gone', true, false],
			['old', 'Old', false, false],
			['b', 'Beta', false, true]
		]);
	});

	it('offers only candidates not yet chosen', () => {
		expect(scopeOptions(scope, ['a'])).toEqual([{ value: 'b', label: 'Beta' }]);
	});

	it('leaves missing presets out of the saved list', () => {
		expect(scopePayload(['a', 'gone', 'b'], scope)).toEqual(['a', 'b']);
	});
});
