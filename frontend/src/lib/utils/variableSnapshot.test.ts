import { describe, it, expect } from 'vitest';
import { buildVariablesSnapshot, buildVariableChipTooltips } from './variableSnapshot';
import type { VariablesMap, VariableRoll } from './variableDefs';

describe('buildVariablesSnapshot', () => {
	it('returns an empty list for undefined variables', () => {
		expect(buildVariablesSnapshot(undefined)).toEqual([]);
	});

	it('serializes a text variable with its value', () => {
		const vars: VariablesMap = { subject: { type: 'text', value: 'a red fox' } };
		expect(buildVariablesSnapshot(vars)).toEqual([
			{ name: 'subject', type: 'text', value: 'a red fox' }
		]);
	});

	it('omits value for an empty text variable', () => {
		const vars: VariablesMap = { subject: { type: 'text', value: '   ' } };
		expect(buildVariablesSnapshot(vars)).toEqual([{ name: 'subject', type: 'text' }]);
	});

	it('serializes a choice variable with options, mode and last roll', () => {
		const vars: VariablesMap = {
			mood: { type: 'choice', options: ['noir', 'sunlit'], mode: 'shuffle', pinnedIndex: null }
		};
		const rolls: Record<string, VariableRoll> = {
			mood: { optionIndex: 1, value: 'sunlit', rolledAt: 1 }
		};
		expect(buildVariablesSnapshot(vars, rolls)).toEqual([
			{ name: 'mood', type: 'choice', options: ['noir', 'sunlit'], mode: 'shuffle', lastRoll: 'sunlit' }
		]);
	});

	it('drops blank options and re-projects the pinned index', () => {
		const vars: VariablesMap = {
			mood: { type: 'choice', options: ['', 'noir', '  ', 'sunlit'], mode: 'pin', pinnedIndex: 3 }
		};
		expect(buildVariablesSnapshot(vars)).toEqual([
			{ name: 'mood', type: 'choice', options: ['noir', 'sunlit'], mode: 'pin', pinnedIndex: 1 }
		]);
	});

	it('skips a choice variable with no valid options', () => {
		const vars: VariablesMap = {
			empty: { type: 'choice', options: ['', ' '], mode: 'shuffle', pinnedIndex: null },
			ok: { type: 'text', value: 'x' }
		};
		expect(buildVariablesSnapshot(vars)).toEqual([{ name: 'ok', type: 'text', value: 'x' }]);
	});

	it('does not emit a last roll for a non-shuffle choice', () => {
		const vars: VariablesMap = {
			mood: { type: 'choice', options: ['a', 'b'], mode: 'per-image', pinnedIndex: null }
		};
		const rolls: Record<string, VariableRoll> = { mood: { optionIndex: 0, value: 'a', rolledAt: 1 } };
		const [entry] = buildVariablesSnapshot(vars, rolls);
		expect(entry.lastRoll).toBeUndefined();
	});

	it('caps the number of variables at 24', () => {
		const vars: VariablesMap = {};
		for (let i = 0; i < 40; i++) vars[`v${i}`] = { type: 'text', value: String(i) };
		expect(buildVariablesSnapshot(vars).length).toBe(24);
	});

	it('caps options at 12', () => {
		const options = Array.from({ length: 20 }, (_, i) => `opt${i}`);
		const vars: VariablesMap = {
			big: { type: 'choice', options, mode: 'per-image', pinnedIndex: null }
		};
		expect(buildVariablesSnapshot(vars)[0].options!.length).toBe(12);
	});

	it('normalizes a legacy bare-string variable', () => {
		const vars: VariablesMap = { legacy: 'plain text' };
		expect(buildVariablesSnapshot(vars)).toEqual([
			{ name: 'legacy', type: 'text', value: 'plain text' }
		]);
	});
});

describe('buildVariableChipTooltips', () => {
	it('returns an empty map for undefined variables', () => {
		expect(buildVariableChipTooltips(undefined)).toEqual({});
	});

	it('describes a choice variable in plain language with the last roll', () => {
		const vars: VariablesMap = {
			mood: { type: 'choice', options: ['noir', 'sunlit'], mode: 'shuffle', pinnedIndex: null }
		};
		const rolls: Record<string, VariableRoll> = {
			mood: { optionIndex: 1, value: 'sunlit', rolledAt: 1 }
		};
		expect(buildVariableChipTooltips(vars, rolls)).toEqual({
			mood: 'one of noir, sunlit — shuffles each generation; last roll: sunlit'
		});
	});

	it('marks a defined-but-empty choice as known (mid-authoring)', () => {
		const vars: VariablesMap = {
			mood: { type: 'choice', options: ['', ''], mode: 'shuffle', pinnedIndex: null }
		};
		const tips = buildVariableChipTooltips(vars);
		expect('mood' in tips).toBe(true);
	});

	it('describes a pinned choice by naming the pinned option', () => {
		const vars: VariablesMap = {
			mood: { type: 'choice', options: ['noir', 'sunlit'], mode: 'pin', pinnedIndex: 1 }
		};
		expect(buildVariableChipTooltips(vars).mood).toBe('one of noir, sunlit — pinned to sunlit');
	});

	it('describes a text variable by its value', () => {
		const vars: VariablesMap = { subject: { type: 'text', value: 'a fox' } };
		expect(buildVariableChipTooltips(vars).subject).toBe('a fox');
	});
});
