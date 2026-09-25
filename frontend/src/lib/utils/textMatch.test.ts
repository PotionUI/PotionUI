import { describe, it, expect } from 'vitest';
import { compileTextMatcher, highlightSegments, matchRanges, textMatches } from './textMatch';

function marked(text: string, query: string, mode: 'contains' | 'regex' = 'contains') {
	return highlightSegments(text, compileTextMatcher(query, mode).matcher)
		.filter((s) => s.match)
		.map((s) => s.text);
}

describe('compileTextMatcher', () => {
	it('returns no matcher and no error for a blank query', () => {
		expect(compileTextMatcher('   ')).toEqual({ matcher: null, error: null });
	});

	it('reports an invalid regular expression instead of throwing', () => {
		const compiled = compileTextMatcher('(dog', 'regex');
		expect(compiled.matcher).toBeNull();
		expect(compiled.error).toMatch(/^Invalid regular expression/);
	});

	it('caps regular expression length', () => {
		expect(compileTextMatcher('a'.repeat(201), 'regex').error).toMatch(/longer than 200/);
		expect(compileTextMatcher('a'.repeat(200), 'regex').matcher).not.toBeNull();
	});

	it('treats regex metacharacters literally in contains mode', () => {
		expect(marked('a.b axb', 'a.b')).toEqual(['a.b']);
	});
});

describe('highlightSegments', () => {
	it('marks a case-insensitive substring and keeps the original casing', () => {
		expect(highlightSegments('Golden Hour', compileTextMatcher('hour').matcher)).toEqual([
			{ text: 'Golden ', match: false },
			{ text: 'Hour', match: true }
		]);
	});

	it('marks every occurrence', () => {
		expect(marked('dog hotdog DOG', 'dog')).toEqual(['dog', 'dog', 'DOG']);
	});

	it('marks regex matches', () => {
		expect(marked('dog dug ding dg', 'd\\w+g', 'regex')).toEqual(['dog', 'dug', 'ding']);
	});

	it('leaves the text unmarked for an empty query', () => {
		expect(highlightSegments('golden hour', compileTextMatcher('').matcher)).toEqual([
			{ text: 'golden hour', match: false }
		]);
	});

	it('leaves the text unmarked for an invalid regex', () => {
		expect(highlightSegments('golden (hour', compileTextMatcher('(hour', 'regex').matcher)).toEqual([
			{ text: 'golden (hour', match: false }
		]);
	});

	it('skips zero-length regex matches without looping', () => {
		expect(matchRanges('axxb', compileTextMatcher('x*', 'regex').matcher)).toEqual([[1, 3]]);
		expect(marked('ab', '^', 'regex')).toEqual([]);
	});

	it('returns nothing for empty text', () => {
		expect(highlightSegments('', compileTextMatcher('a').matcher)).toEqual([]);
	});
});

describe('textMatches', () => {
	it('passes everything without a matcher', () => {
		expect(textMatches(null, 'anything')).toBe(true);
	});

	it('matches when any of the texts match', () => {
		const matcher = compileTextMatcher('^warm', 'regex').matcher;
		expect(textMatches(matcher, 'golden hour', 'warm rim light')).toBe(true);
		expect(textMatches(matcher, 'golden warm', undefined)).toBe(false);
	});
});
