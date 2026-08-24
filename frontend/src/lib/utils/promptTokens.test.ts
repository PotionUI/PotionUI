import { describe, it, expect } from 'vitest';
import { parsePromptTokens } from './promptTokens';

function rebuild(text: string): string {
	return parsePromptTokens(text)
		.map((t) => t.raw)
		.join('');
}

describe('parsePromptTokens', () => {
	it('returns a single text token for plain text', () => {
		const tokens = parsePromptTokens('a plain prompt');
		expect(tokens).toEqual([{ type: 'text', raw: 'a plain prompt', start: 0, end: 14 }]);
	});

	it('tokenizes a lone choice group', () => {
		const tokens = parsePromptTokens('a photo of {cat|dog}');
		expect(tokens.map((t) => t.type)).toEqual(['text', 'group']);
		expect(tokens[1]).toMatchObject({ type: 'group', raw: '{cat|dog}' });
	});

	it('tokenizes a lone variable usage', () => {
		const tokens = parsePromptTokens('a photo, ${mood} lighting');
		expect(tokens.map((t) => t.type)).toEqual(['text', 'variable', 'text']);
		expect(tokens[1]).toMatchObject({ type: 'variable', raw: '${mood}', name: 'mood' });
	});

	it('tokenizes a group followed by a variable', () => {
		const tokens = parsePromptTokens('{a|b} and ${mood}');
		expect(tokens.map((t) => t.type)).toEqual(['group', 'text', 'variable']);
		expect(tokens[0]).toMatchObject({ type: 'group', raw: '{a|b}' });
		expect(tokens[2]).toMatchObject({ type: 'variable', raw: '${mood}', name: 'mood' });
	});

	it('tokenizes a variable followed by a group, with correct absolute offsets', () => {
		const text = '${mood} and {a|b}';
		const tokens = parsePromptTokens(text);
		expect(tokens.map((t) => t.type)).toEqual(['variable', 'text', 'group']);

		const variable = tokens[0];
		expect(variable).toMatchObject({ raw: '${mood}', start: 0, end: 7, name: 'mood' });

		const group = tokens[2];
		expect(group).toMatchObject({ raw: '{a|b}', start: 12, end: 17 });
	});

	it('handles several groups and variables interleaved', () => {
		const text = '${a} {b|c} plain ${d} {e|f}';
		const tokens = parsePromptTokens(text);
		expect(tokens.map((t) => t.type)).toEqual([
			'variable',
			'text',
			'group',
			'text',
			'variable',
			'text',
			'group'
		]);
	});

	it('does not treat ${name} as a group even when adjacent to a real group (regression)', () => {
		const tokens = parsePromptTokens('${mood} {a|b}');
		const groups = tokens.filter((t) => t.type === 'group');
		const variables = tokens.filter((t) => t.type === 'variable');
		expect(groups).toHaveLength(1);
		expect(groups[0]).toMatchObject({ raw: '{a|b}' });
		expect(variables).toHaveLength(1);
		expect(variables[0]).toMatchObject({ raw: '${mood}', name: 'mood' });
	});

	it('leaves a ${name} embedded inside a group option opaque (part of the group, not its own token)', () => {
		const text = '{a|${mood} b}';
		const tokens = parsePromptTokens(text);
		expect(tokens).toHaveLength(1);
		expect(tokens[0]).toMatchObject({ type: 'group', raw: text });
	});

	// --- Round-trip property: tokens.map(t => t.raw).join('') === text -------
	const roundTripCases = [
		'',
		'a plain prompt',
		'a photo of {cat|dog|bird}',
		'a photo, ${mood} lighting',
		'{a|b} and ${mood}',
		'${mood} and {a|b}',
		'${a} {b|c} plain ${d} {e|f}',
		'{0.5::a|0.3::b|c} and ${era}',
		'${mood}${era}', // back-to-back usages, no separator
		'{a|b}{c|d}', // back-to-back groups, no separator
		'unterminated ${broken',
		'unterminated {broken',
		'$ not a variable, {not a group'
	];

	for (const text of roundTripCases) {
		it(`round-trips ${JSON.stringify(text)}`, () => {
			expect(rebuild(text)).toBe(text);
		});
	}

	it('round-trips arbitrary mixed content byte-for-byte', () => {
		const text = 'A ${style} portrait of a {cat|dog|${default}} wearing a {red|blue|green} hat, ${mood} mood.';
		expect(rebuild(text)).toBe(text);
	});
});
