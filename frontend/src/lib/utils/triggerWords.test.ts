import { describe, expect, it } from 'vitest';
import {
	findTriggerWordMatches,
	hasTriggerWordMatch,
	mergeTriggerWords,
	parseTriggerWords
} from './triggerWords';

describe('trigger word list parsing', () => {
	it('imports comma and newline separated values', () => {
		expect(parseTriggerWords('portrait, cinematic\nsoft light\r\n35mm')).toEqual([
			'portrait',
			'cinematic',
			'soft light',
			'35mm'
		]);
	});

	it('drops blanks and exact duplicates while preserving order', () => {
		expect(parseTriggerWords(' alpha,\n beta,alpha,  ')).toEqual(['alpha', 'beta']);
	});

	it('does not duplicate values already in the editor', () => {
		expect(mergeTriggerWords(['alpha'], 'beta\nalpha,gamma')).toEqual([
			'alpha',
			'beta',
			'gamma'
		]);
	});
});

describe('findTriggerWordMatches', () => {
	it('finds a single-word trigger', () => {
		expect(findTriggerWordMatches('a portrait of a knight', ['knight'])).toEqual([
			{ start: 16, end: 22, trigger: 'knight' }
		]);
	});

	it('matches multi-word triggers as a contiguous phrase', () => {
		const matches = findTriggerWordMatches('cinematic soft light, 35mm lens', ['soft light']);
		expect(matches).toEqual([{ start: 10, end: 20, trigger: 'soft light' }]);
	});

	it('is case-insensitive and preserves the matched text\'s original casing', () => {
		const matches = findTriggerWordMatches('A Knight in SHINING armor', ['knight', 'shining']);
		expect(matches.map((m) => m.trigger)).toEqual(['Knight', 'SHINING']);
	});

	it('matches without word boundaries, same as the picker\'s substring check', () => {
		expect(findTriggerWordMatches('unknighted', ['knight'])).toEqual([
			{ start: 2, end: 8, trigger: 'knight' }
		]);
	});

	it('finds every occurrence of a repeated trigger', () => {
		const matches = findTriggerWordMatches('red fox, red hat, red car', ['red']);
		expect(matches).toEqual([
			{ start: 0, end: 3, trigger: 'red' },
			{ start: 9, end: 12, trigger: 'red' },
			{ start: 18, end: 21, trigger: 'red' }
		]);
	});

	it('resolves overlapping triggers by keeping the earliest match and preferring the longer one at the same start', () => {
		// "soft light" contains "light" - the multi-word trigger should win at
		// that position rather than double-highlighting a sub-range of it.
		const matches = findTriggerWordMatches('soft light', ['light', 'soft light']);
		expect(matches).toEqual([{ start: 0, end: 10, trigger: 'soft light' }]);
	});

	it('does not let an earlier, shorter match block a later, non-overlapping one', () => {
		const matches = findTriggerWordMatches('cat, category', ['cat', 'category']);
		expect(matches).toEqual([
			{ start: 0, end: 3, trigger: 'cat' },
			{ start: 5, end: 13, trigger: 'category' }
		]);
	});

	it('escapes regex-special characters in trigger words', () => {
		const text = 'style: 1.5x (retro sci-fi) [remastered]';
		const matches = findTriggerWordMatches(text, ['1.5x', '(retro sci-fi)', '[remastered]']);
		expect(matches.map((m) => m.trigger)).toEqual(['1.5x', '(retro sci-fi)', '[remastered]']);
	});

	it('ignores blank/whitespace-only triggers and dedupes identical ones', () => {
		expect(findTriggerWordMatches('knight', ['knight', 'knight', '', '   '])).toEqual([
			{ start: 0, end: 6, trigger: 'knight' }
		]);
	});

	it('returns no matches for empty text or an empty trigger list', () => {
		expect(findTriggerWordMatches('', ['knight'])).toEqual([]);
		expect(findTriggerWordMatches('a knight', [])).toEqual([]);
	});
});

describe('hasTriggerWordMatch', () => {
	it('mirrors findTriggerWordMatches as a boolean', () => {
		expect(hasTriggerWordMatch('A KNIGHT stands here', 'knight')).toBe(true);
		expect(hasTriggerWordMatch('a wizard stands here', 'knight')).toBe(false);
	});
});
