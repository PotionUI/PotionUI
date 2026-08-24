import { describe, it, expect } from 'vitest';
import type { ChipData } from '$lib/types/segments';
import type { VariablesMap, VariableRoll } from '$lib/utils/variableDefs';
import { variablePreview, getChipValueMap, getChipsHash, hashVariableRolls } from './chipEditorHelpers';

function makeChip(overrides: Partial<ChipData> = {}): ChipData {
	return {
		id: 'chip-1',
		categoryPath: 'emotions.happy',
		valueId: 'val-1',
		label: 'Happy',
		value: 'happy',
		allValues: [],
		shuffle: false,
		autoRegen: false,
		...overrides
	};
}

describe('variablePreview', () => {
	const variables: VariablesMap = {
		text_var: { type: 'text', value: 'a raw value' },
		choice_var: { type: 'choice', options: ['a', 'b', ' '], mode: 'shuffle', pinnedIndex: null },
		pinned_var: { type: 'choice', options: ['a', 'b'], mode: 'pin', pinnedIndex: 1 },
		empty_choice: { type: 'choice', options: ['', ' '], mode: 'shuffle', pinnedIndex: null },
		legacy_string: 'a legacy bare string'
	};

	it('previews a text variable as its raw value', () => {
		expect(variablePreview('text_var', variables)).toBe('a raw value');
	});

	it('previews an undefined variable as an empty text default', () => {
		expect(variablePreview('nope', variables)).toBe('');
	});

	it('previews a shuffle-mode choice variable as its pipe-joined, blank-filtered options', () => {
		expect(variablePreview('choice_var', variables)).toBe('a | b');
	});

	it('previews a pin-mode choice variable as "pinned: <option>"', () => {
		expect(variablePreview('pinned_var', variables)).toBe('pinned: b');
	});

	it('previews a choice variable with no non-blank options as a placeholder', () => {
		expect(variablePreview('empty_choice', variables)).toBe('(no options yet)');
	});

	it('normalizes a legacy bare-string variable to a text preview', () => {
		expect(variablePreview('legacy_string', variables)).toBe('a legacy bare string');
	});
});

describe('getChipValueMap', () => {
	it('maps each chip id to its valueId', () => {
		const chips = {
			a: makeChip({ id: 'a', valueId: 'v1' }),
			b: makeChip({ id: 'b', valueId: 'v2' })
		};
		expect(getChipValueMap(chips)).toEqual({ a: 'v1', b: 'v2' });
	});

	it('returns an empty object for an empty chip map', () => {
		expect(getChipValueMap({})).toEqual({});
	});
});

describe('getChipsHash', () => {
	it('is stable across key insertion order (sorted)', () => {
		const first = { a: makeChip({ id: 'a', valueId: 'v1' }), b: makeChip({ id: 'b', valueId: 'v2' }) };
		const second = { b: makeChip({ id: 'b', valueId: 'v2' }), a: makeChip({ id: 'a', valueId: 'v1' }) };
		expect(getChipsHash(first)).toBe(getChipsHash(second));
	});

	it('changes when any chip valueId changes', () => {
		const before = { a: makeChip({ id: 'a', valueId: 'v1' }) };
		const after = { a: makeChip({ id: 'a', valueId: 'v2' }) };
		expect(getChipsHash(before)).not.toBe(getChipsHash(after));
	});

	it('is the empty string for an empty chip map', () => {
		expect(getChipsHash({})).toBe('');
	});
});

describe('hashVariableRolls', () => {
	it('is stable across key insertion order (sorted)', () => {
		const rollA: VariableRoll = { optionIndex: 0, value: 'x', rolledAt: 100 };
		const rollB: VariableRoll = { optionIndex: 1, value: 'y', rolledAt: 200 };
		expect(hashVariableRolls({ a: rollA, b: rollB })).toBe(hashVariableRolls({ b: rollB, a: rollA }));
	});

	it('changes when rolledAt changes even if optionIndex/value are the same', () => {
		const before = { a: { optionIndex: 0, value: 'x', rolledAt: 100 } };
		const after = { a: { optionIndex: 0, value: 'x', rolledAt: 200 } };
		expect(hashVariableRolls(before)).not.toBe(hashVariableRolls(after));
	});

	it('is the empty string for no rolls', () => {
		expect(hashVariableRolls({})).toBe('');
	});
});
