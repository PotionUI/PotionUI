import { describe, it, expect } from 'vitest';
import {
	normalizeVariableDef,
	createTextVariable,
	createChoiceVariable,
	buildVariableWireValue,
	buildVariablesForSubmit,
	rollChoiceOption,
	resolveVariableChipState,
	hashVariablesMap,
	stepVariablesHash,
	type ChoiceVariableDef,
	type LegacyChoiceVariableDef
} from './variableDefs';

describe('normalizeVariableDef', () => {
	it('reads a legacy bare string as a text variable', () => {
		expect(normalizeVariableDef('{noir|sunlit}')).toEqual({ type: 'text', value: '{noir|sunlit}' });
	});

	it('passes a typed text def through unchanged', () => {
		const def = createTextVariable('noir');
		expect(normalizeVariableDef(def)).toBe(def);
	});

	it('passes a typed choice def (with mode) through with the same content', () => {
		const def: ChoiceVariableDef = { type: 'choice', options: ['a', 'b'], mode: 'pin', pinnedIndex: 1 };
		expect(normalizeVariableDef(def)).toEqual(def);
	});

	it('defaults an undefined entry to an empty text variable', () => {
		expect(normalizeVariableDef(undefined)).toEqual({ type: 'text', value: '' });
	});

	// Migration: a choice def with no `mode` field defaults to `shuffle`,
	// EXCEPT when it already had an explicit pin, which carries over as `pin`.
	describe('legacy choice migration (no `mode` field)', () => {
		it('migrates an un-pinned legacy choice variable to shuffle mode (not per-image)', () => {
			const legacy: LegacyChoiceVariableDef = { type: 'choice', options: ['a', 'b'], pinnedIndex: null };
			expect(normalizeVariableDef(legacy)).toEqual({
				type: 'choice',
				options: ['a', 'b'],
				mode: 'shuffle',
				pinnedIndex: null
			});
		});

		it('migrates a pinned legacy choice variable to pin mode, preserving the pinned index', () => {
			const legacy: LegacyChoiceVariableDef = { type: 'choice', options: ['a', 'b', 'c'], pinnedIndex: 2 };
			expect(normalizeVariableDef(legacy)).toEqual({
				type: 'choice',
				options: ['a', 'b', 'c'],
				mode: 'pin',
				pinnedIndex: 2
			});
		});
	});
});

describe('createTextVariable / createChoiceVariable', () => {
	it('creates an empty text variable by default', () => {
		expect(createTextVariable()).toEqual({ type: 'text', value: '' });
	});

	it('creates a choice variable in shuffle mode with two blank options by default', () => {
		expect(createChoiceVariable()).toEqual({ type: 'choice', options: ['', ''], mode: 'shuffle', pinnedIndex: null });
	});
});

describe('buildVariableWireValue', () => {
	it('passes a text variable value through unchanged, including hand-typed notation', () => {
		expect(buildVariableWireValue(createTextVariable('{noir|sunlit}'))).toBe('{noir|sunlit}');
		expect(buildVariableWireValue(createTextVariable('plain'))).toBe('plain');
	});

	it('serializes a shuffle-mode choice variable to {a|b|c} as a fallback/preview (actual submission rolls instead — see buildVariablesForSubmit)', () => {
		expect(buildVariableWireValue(createChoiceVariable(['red', 'green', 'blue']))).toBe('{red|green|blue}');
	});

	it('serializes a per-image-mode choice variable to {a|b|c} — the advanced "backend rolls per image" escape hatch', () => {
		const def: ChoiceVariableDef = { type: 'choice', options: ['red', 'green', 'blue'], mode: 'per-image', pinnedIndex: null };
		expect(buildVariableWireValue(def)).toBe('{red|green|blue}');
	});

	it('ignores blank option rows when serializing', () => {
		expect(buildVariableWireValue(createChoiceVariable(['red', '', 'blue']))).toBe('{red|blue}');
	});

	it('collapses to the plain option text when there is exactly one valid option (no braces needed)', () => {
		expect(buildVariableWireValue(createChoiceVariable(['only', '']))).toBe('only');
	});

	it('resolves a choice variable with no valid options to an empty string', () => {
		expect(buildVariableWireValue(createChoiceVariable(['', '']))).toBe('');
	});

	it('emits the pinned option alone, with no braces, when mode is pin', () => {
		const def: ChoiceVariableDef = { type: 'choice', options: ['red', 'green', 'blue'], mode: 'pin', pinnedIndex: 1 };
		expect(buildVariableWireValue(def)).toBe('green');
	});

	it('falls back to the grouped form across remaining options when pin mode has a blank pinned index', () => {
		const def: ChoiceVariableDef = { type: 'choice', options: ['red', '', 'blue'], mode: 'pin', pinnedIndex: 1 };
		expect(buildVariableWireValue(def)).toBe('{red|blue}');
	});

	it('ignores pinnedIndex when mode is not pin', () => {
		// A stale pinnedIndex left over from a previous pin shouldn't leak into
		// shuffle/per-image serialization — only `mode` decides.
		const def: ChoiceVariableDef = { type: 'choice', options: ['red', 'green'], mode: 'shuffle', pinnedIndex: 0 };
		expect(buildVariableWireValue(def)).toBe('{red|green}');
	});
});

describe('rollChoiceOption', () => {
	it('returns null when there are no valid options', () => {
		expect(rollChoiceOption(createChoiceVariable(['', '']))).toBeNull();
	});

	it('deterministically picks by index with an injected random source', () => {
		const def = createChoiceVariable(['red', 'green', 'blue']);
		expect(rollChoiceOption(def, () => 0)).toEqual({ index: 0, value: 'red' });
		expect(rollChoiceOption(def, () => 0.5)).toEqual({ index: 1, value: 'green' });
		expect(rollChoiceOption(def, () => 0.99)).toEqual({ index: 2, value: 'blue' });
	});

	it('only picks among non-blank options, but reports the option array index (not the compacted index)', () => {
		const def = createChoiceVariable(['', 'green', '']);
		expect(rollChoiceOption(def, () => 0)).toEqual({ index: 1, value: 'green' });
	});

	it('always resolves to the single valid option when there is only one', () => {
		const def = createChoiceVariable(['only', '']);
		expect(rollChoiceOption(def, () => 0.9)).toEqual({ index: 0, value: 'only' });
	});
});

describe('buildVariablesForSubmit', () => {
	it('returns an empty result for undefined variables', () => {
		expect(buildVariablesForSubmit(undefined)).toEqual({ wireMap: {}, rolls: {} });
	});

	it('normalizes a mix of legacy strings and typed defs into the same wire shape', () => {
		const { wireMap } = buildVariablesForSubmit({
			mood: '{noir|sunlit}', // legacy shape, resolved as text (no roll)
			era: createTextVariable('victorian'),
			frame: { type: 'choice', options: ['wide', 'close'], mode: 'per-image', pinnedIndex: null }
		});
		expect(wireMap).toEqual({
			mood: '{noir|sunlit}',
			era: 'victorian',
			frame: '{wide|close}'
		});
	});

	it('omits a variable that resolves to nothing rather than sending an empty string', () => {
		const { wireMap } = buildVariablesForSubmit({
			empty: createTextVariable(''),
			brokenChoice: { type: 'choice', options: ['', ''], mode: 'per-image', pinnedIndex: null },
			mood: createTextVariable('noir')
		});
		expect(wireMap).toEqual({ mood: 'noir' });
		expect(wireMap).not.toHaveProperty('empty');
		expect(wireMap).not.toHaveProperty('brokenChoice');
	});

	it('rolls a shuffle-mode choice variable ONCE, sending the plain rolled value with no braces', () => {
		const { wireMap, rolls } = buildVariablesForSubmit(
			{ palette: createChoiceVariable(['warm', 'cool']) },
			{ random: () => 0.9, now: () => 12345 }
		);
		expect(wireMap.palette).toBe('cool'); // index 1 for random()=0.9 of 2 options
		expect(wireMap.palette).not.toMatch(/[{}]/);
		expect(rolls.palette).toEqual({ optionIndex: 1, value: 'cool', rolledAt: 12345 });
	});

	it('the rolled value in `rolls` always matches what actually landed in `wireMap` — the Generate-click contract', () => {
		const { wireMap, rolls } = buildVariablesForSubmit(
			{ mood: createChoiceVariable(['noir', 'sunlit', 'pastel']) },
			{ random: () => 0.4 }
		);
		expect(wireMap.mood).toBe(rolls.mood.value);
	});

	it('re-rolls independently on every call (two calls can disagree)', () => {
		const variables = { mood: createChoiceVariable(['a', 'b', 'c', 'd', 'e']) };
		const first = buildVariablesForSubmit(variables, { random: () => 0 });
		const second = buildVariablesForSubmit(variables, { random: () => 0.99 });
		expect(first.wireMap.mood).toBe('a');
		expect(second.wireMap.mood).toBe('e');
	});

	it('does not roll (and reports no roll) for pin or per-image mode', () => {
		const { wireMap, rolls } = buildVariablesForSubmit({
			pinned: { type: 'choice', options: ['red', 'green'], mode: 'pin', pinnedIndex: 0 },
			perImage: { type: 'choice', options: ['red', 'green'], mode: 'per-image', pinnedIndex: null }
		});
		expect(wireMap).toEqual({ pinned: 'red', perImage: '{red|green}' });
		expect(rolls).toEqual({});
	});

	it('does not produce a roll entry for a shuffle variable with no valid options', () => {
		const { wireMap, rolls } = buildVariablesForSubmit({
			broken: createChoiceVariable(['', ''])
		});
		expect(wireMap).toEqual({});
		expect(rolls).toEqual({});
	});
});

describe('resolveVariableChipState', () => {
	it('returns null (undefined/warning state) when the variable has no entry at all', () => {
		expect(resolveVariableChipState('mood', {})).toBeNull();
		expect(resolveVariableChipState('mood', undefined)).toBeNull();
	});

	it('returns the normalized def for a defined text variable, even when its value is blank', () => {
		expect(resolveVariableChipState('mood', { mood: createTextVariable('') })).toEqual({
			type: 'text',
			value: ''
		});
	});

	it('returns the normalized def for a defined choice variable, even with no valid options yet', () => {
		expect(resolveVariableChipState('palette', { palette: createChoiceVariable(['', '']) })).toEqual({
			type: 'choice',
			options: ['', ''],
			mode: 'shuffle',
			pinnedIndex: null
		});
	});

	it('normalizes a legacy bare-string definition', () => {
		expect(resolveVariableChipState('mood', { mood: '{noir|sunlit}' })).toEqual({
			type: 'text',
			value: '{noir|sunlit}'
		});
	});

	it('does not confuse an unrelated defined variable with the one being looked up', () => {
		expect(resolveVariableChipState('era', { mood: createTextVariable('noir') })).toBeNull();
	});
});

describe('hashVariablesMap', () => {
	it('hashes an empty map to the empty string — a legitimate value, not a sentinel (see stepVariablesHash)', () => {
		expect(hashVariablesMap({})).toBe('');
	});

	it('produces different hashes for different maps', () => {
		const a = hashVariablesMap({ mood: createTextVariable('noir') });
		const b = hashVariablesMap({ mood: createTextVariable('sunlit') });
		expect(a).not.toBe(b);
	});

	it('is order-independent (same content, different insertion order, same hash)', () => {
		const a = hashVariablesMap({ mood: createTextVariable('noir'), era: createTextVariable('victorian') });
		const b = hashVariablesMap({ era: createTextVariable('victorian'), mood: createTextVariable('noir') });
		expect(a).toBe(b);
	});
});

describe('stepVariablesHash', () => {
	it('never remounts on the first observation, regardless of hash value', () => {
		expect(stepVariablesHash('', null)).toEqual({ shouldRemount: false, nextHash: '' });
		expect(stepVariablesHash('mood:{"type":"text","value":"noir"}', null)).toEqual({
			shouldRemount: false,
			nextHash: 'mood:{"type":"text","value":"noir"}'
		});
	});

	it('remounts when the hash actually changes', () => {
		expect(stepVariablesHash('b', 'a')).toEqual({ shouldRemount: true, nextHash: 'b' });
	});

	it('does not remount when the hash is unchanged', () => {
		expect(stepVariablesHash('a', 'a')).toEqual({ shouldRemount: false, nextHash: 'a' });
	});

	// The exact regression this was built to catch: a tab starts with NO
	// variables at all (hash === ''), a `${name}` usage chip mounts showing
	// "undefined" warning styling, and then the variable gets defined (e.g. a
	// session load). The transition is '' -> 'mood:...', and '' must NOT be
	// mistaken for "uninitialized" (only `null` means that) or this second
	// step would wrongly report `shouldRemount: false` and the chip's
	// warning styling would never clear.
	it('regression: an empty-map baseline still detects the very next real change', () => {
		const firstHash = hashVariablesMap({}); // tab starts with no variables
		const seed = stepVariablesHash(firstHash, null);
		expect(seed).toEqual({ shouldRemount: false, nextHash: '' });

		const secondHash = hashVariablesMap({ mood: createTextVariable('noir') }); // variable just got defined
		const next = stepVariablesHash(secondHash, seed.nextHash);
		expect(next.shouldRemount).toBe(true);
	});
});
