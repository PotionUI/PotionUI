import { describe, it, expect } from 'vitest';
import {
	parseGroupInner,
	serializeGroup,
	parseChoiceGroupTokens,
	countChoiceGroups,
	spliceGroupText,
	type ChoiceGroupSpec
} from './choiceGroups';

describe('parseGroupInner', () => {
	it('parses a plain alternation', () => {
		expect(parseGroupInner('a|b|c')).toEqual({
			options: [
				{ text: 'a', weight: null },
				{ text: 'b', weight: null },
				{ text: 'c', weight: null }
			],
			count: null,
			countMax: null,
			separator: null
		});
	});

	it('parses weighted options, defaulting omitted weights to null', () => {
		expect(parseGroupInner('0.5::a|0.3::b|c')).toEqual({
			options: [
				{ text: 'a', weight: 0.5 },
				{ text: 'b', weight: 0.3 },
				{ text: 'c', weight: null }
			],
			count: null,
			countMax: null,
			separator: null
		});
	});

	it('parses a pick-N prefix without a separator', () => {
		expect(parseGroupInner('2$$a|b|c')).toEqual({
			options: [
				{ text: 'a', weight: null },
				{ text: 'b', weight: null },
				{ text: 'c', weight: null }
			],
			count: 2,
			countMax: null,
			separator: null
		});
	});

	it('parses a pick-N prefix with a custom separator', () => {
		expect(parseGroupInner('2$$ and $$a|b|c')).toEqual({
			options: [
				{ text: 'a', weight: null },
				{ text: 'b', weight: null },
				{ text: 'c', weight: null }
			],
			count: 2,
			countMax: null,
			separator: ' and '
		});
	});

	it('parses a range pick prefix', () => {
		expect(parseGroupInner('1-2$$a|b|c')).toEqual({
			options: [
				{ text: 'a', weight: null },
				{ text: 'b', weight: null },
				{ text: 'c', weight: null }
			],
			count: 1,
			countMax: 2,
			separator: null
		});
	});

	it('rejects an empty group', () => {
		expect(parseGroupInner('')).toBeNull();
	});

	it('keeps a nested group literal inside an option', () => {
		const spec = parseGroupInner('a|{x|y} b');
		expect(spec?.options).toEqual([
			{ text: 'a', weight: null },
			{ text: '{x|y} b', weight: null }
		]);
	});
});

describe('serializeGroup', () => {
	it('round-trips a plain alternation', () => {
		const spec = parseGroupInner('a|b|c')!;
		expect(serializeGroup(spec)).toBe('{a|b|c}');
	});

	it('round-trips weighted options and omits a weight of exactly 1', () => {
		const spec: ChoiceGroupSpec = {
			options: [
				{ text: 'a', weight: 0.5 },
				{ text: 'b', weight: 1 },
				{ text: 'c', weight: null }
			],
			count: null,
			countMax: null,
			separator: null
		};
		expect(serializeGroup(spec)).toBe('{0.5::a|b|c}');
	});

	it('round-trips a pick-N group without a separator', () => {
		expect(serializeGroup(parseGroupInner('2$$a|b|c')!)).toBe('{2$$a|b|c}');
	});

	it('round-trips a pick-N group with a custom separator', () => {
		expect(serializeGroup(parseGroupInner('2$$ and $$a|b|c')!)).toBe('{2$$ and $$a|b|c}');
	});

	it('round-trips a range-pick group', () => {
		expect(serializeGroup(parseGroupInner('1-2$$a|b|c')!)).toBe('{1-2$$a|b|c}');
	});

	it('formats float weights without binary noise', () => {
		const spec: ChoiceGroupSpec = {
			options: [
				{ text: 'a', weight: 0.3 },
				{ text: 'b', weight: null }
			],
			count: null,
			countMax: null,
			separator: null
		};
		expect(serializeGroup(spec)).toBe('{0.3::a|b}');
	});
});

describe('round-trip property: serialize(parse(text)) === text for canonical text', () => {
	const canonicalExamples = [
		'{a|b|c}',
		'{0.5::a|0.3::b|c}',
		'{2$$a|b|c}',
		'{2$$ and $$a|b|c}',
		'{1-2$$a|b|c}',
		'{red|green|blue}',
		'{a|2::b}'
	];

	for (const raw of canonicalExamples) {
		it(`round-trips ${JSON.stringify(raw)}`, () => {
			const inner = raw.slice(1, -1);
			const spec = parseGroupInner(inner);
			expect(spec).not.toBeNull();
			expect(serializeGroup(spec!)).toBe(raw);
		});
	}

	it('round-trips through the full tokenizer for a prompt containing a group', () => {
		const text = 'a photo of {a cat|a dog|a bird} sitting on a chair';
		const tokens = parseChoiceGroupTokens(text);
		const rebuilt = tokens.map((t) => t.raw).join('');
		expect(rebuilt).toBe(text);

		const group = tokens.find((t) => t.type === 'group');
		expect(group).toBeDefined();
		if (group?.type === 'group') {
			expect(serializeGroup(group.spec)).toBe(group.raw);
		}
	});

	it('round-trips parse -> serialize -> parse to an identical structured spec (not just text)', () => {
		const original: ChoiceGroupSpec = {
			options: [
				{ text: 'a', weight: 0.7 },
				{ text: 'b c', weight: null },
				{ text: 'd', weight: 2 }
			],
			count: 2,
			countMax: 3,
			separator: ', then '
		};
		const raw = serializeGroup(original);
		const reparsed = parseGroupInner(raw.slice(1, -1));
		expect(reparsed).toEqual(original);
	});
});

describe('parseChoiceGroupTokens', () => {
	it('returns a single text token for plain text', () => {
		const tokens = parseChoiceGroupTokens('a plain prompt');
		expect(tokens).toEqual([{ type: 'text', raw: 'a plain prompt', start: 0, end: 14 }]);
	});

	it('splits text around a group, preserving exact spans', () => {
		const text = 'before {a|b} after';
		const tokens = parseChoiceGroupTokens(text);
		expect(tokens.map((t) => t.raw)).toEqual(['before ', '{a|b}', ' after']);
		expect(tokens.map((t) => t.type)).toEqual(['text', 'group', 'text']);
		// Spans are contiguous and reconstruct the source.
		expect(tokens.map((t) => t.raw).join('')).toBe(text);
	});

	it('handles multiple groups in one string', () => {
		const text = '{a|b} and {c|d}';
		const tokens = parseChoiceGroupTokens(text);
		expect(tokens.filter((t) => t.type === 'group')).toHaveLength(2);
		expect(tokens.map((t) => t.raw).join('')).toBe(text);
	});

	it('degrades an unbalanced brace to plain text rather than throwing', () => {
		const text = 'a {unbalanced prompt';
		const tokens = parseChoiceGroupTokens(text);
		expect(tokens.every((t) => t.type === 'text')).toBe(true);
		expect(tokens.map((t) => t.raw).join('')).toBe(text);
	});

	it('degrades an empty group {} to plain text', () => {
		const text = 'nothing {} here';
		const tokens = parseChoiceGroupTokens(text);
		expect(tokens.every((t) => t.type === 'text')).toBe(true);
	});

	it('keeps a nested group as part of the outer group, not a separate token', () => {
		const text = '{a|{x|y}}';
		const tokens = parseChoiceGroupTokens(text);
		expect(tokens).toHaveLength(1);
		expect(tokens[0]).toMatchObject({ type: 'group', raw: text });
	});

	// Regression: ${name} (variable USAGE syntax, see promptVariables.ts) must
	// never be mistaken for a choice group. `{name}` alone (no `|`) is
	// otherwise a syntactically valid single-option group, so without an
	// explicit `$`-prefix exclusion, "${mood}" chip-ified into a stray literal
	// "$" followed by a group chip reading "mood".
	it('does not treat ${name} as a choice group', () => {
		const text = 'a photo, ${mood} lighting';
		const tokens = parseChoiceGroupTokens(text);
		expect(tokens.every((t) => t.type === 'text')).toBe(true);
		expect(tokens.map((t) => t.raw).join('')).toBe(text);
	});

	it('does not treat a $-prefixed group as a choice group even with pipes inside', () => {
		expect(countChoiceGroups('${a|b|c}')).toBe(0);
	});

	it('still recognizes a real choice group immediately after a variable usage', () => {
		const text = '${mood} and {a|b}';
		const tokens = parseChoiceGroupTokens(text);
		const groups = tokens.filter((t) => t.type === 'group');
		expect(groups).toHaveLength(1);
		expect(groups[0].raw).toBe('{a|b}');
	});

	it('does not treat a bare {name} as a group when not $-prefixed (sanity: the exclusion is $-specific)', () => {
		expect(countChoiceGroups('{mood}')).toBe(1);
	});
});

describe('countChoiceGroups', () => {
	it('counts zero for plain text', () => {
		expect(countChoiceGroups('nothing here')).toBe(0);
	});

	it('counts one right after a group closes', () => {
		expect(countChoiceGroups('a photo of {cat|dog}')).toBe(1);
	});

	it('does not count an unclosed group', () => {
		expect(countChoiceGroups('a photo of {cat|dog')).toBe(0);
	});

	it('counts multiple independent groups', () => {
		expect(countChoiceGroups('{a|b} and {c|d} and {e|f}')).toBe(3);
	});
});

describe('spliceGroupText', () => {
	it('replaces exactly the token span, leaving the rest untouched', () => {
		const text = 'before {a|b} after';
		const [token] = parseChoiceGroupTokens(text).filter((t) => t.type === 'group');
		const result = spliceGroupText(text, token, '{a|b|c}');
		expect(result).toBe('before {a|b|c} after');
	});
});
