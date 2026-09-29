import { describe, it, expect } from 'vitest';
import { findBannedWord, formatBannedWords, parseBannedWords } from './bannedWords';

describe('parseBannedWords', () => {
	it('normalizes, trims, drops blanks and duplicates', () => {
		expect(parseBannedWords('Nud*\n\n  Red   Flag \r\nnud*\n')).toEqual(['nud*', 'red flag']);
	});

	it('folds full-width and compatibility characters (NFKC)', () => {
		expect(parseBannedWords('ＮＵＤＥ')).toEqual(['nude']);
	});

	it('round-trips through formatBannedWords', () => {
		expect(parseBannedWords(formatBannedWords(['a', 'b c']))).toEqual(['a', 'b c']);
		expect(formatBannedWords(undefined)).toBe('');
	});
});

describe('findBannedWord', () => {
	it('matches whole words only', () => {
		expect(findBannedWord('a classic scene', ['ass'])).toBeNull();
		expect(findBannedWord('an ass, standing', ['ass'])).toBe('ass');
	});

	it('is case-insensitive and NFKC-normalized', () => {
		expect(findBannedWord('A NUDE study', ['nude'])).toBe('nude');
		expect(findBannedWord('ＮＵＤＥ study', ['nude'])).toBe('nude');
	});

	it('supports a trailing, leading and inner wildcard', () => {
		expect(findBannedWord('nudity', ['nud*'])).toBe('nud*');
		expect(findBannedWord('a nudist', ['nud*'])).toBe('nud*');
		expect(findBannedWord('unnude', ['*nude'])).toBe('*nude');
		expect(findBannedWord('gore', ['g*e'])).toBe('g*e');
		expect(findBannedWord('a nun', ['nud*'])).toBeNull();
	});

	it('matches multi-word phrases', () => {
		expect(findBannedWord('a red flag here', ['red flag'])).toBe('red flag');
		expect(findBannedWord('red  flag', ['red flag'])).toBeNull();
	});

	it('treats regex metacharacters literally', () => {
		expect(findBannedWord('a (b) c', ['(b)'])).toBe('(b)');
		expect(findBannedWord('axb', ['a.b'])).toBeNull();
	});

	it('returns null for an empty list and ignores wildcard-only entries', () => {
		expect(findBannedWord('anything', [])).toBeNull();
		expect(findBannedWord('anything', ['*'])).toBeNull();
	});
});
