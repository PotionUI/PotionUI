import { describe, it, expect } from 'vitest';
import {
	isValidVariableName,
	variableUsageSyntax,
	detectVariableTrigger,
	filterVariableNames,
	insertVariableUsage,
	extractVariableUsages,
	findUndefinedVariableUsages,
	parseVariableUsageTokens,
	countVariableUsages
} from './promptVariables';

describe('isValidVariableName', () => {
	it('accepts identifier-like names', () => {
		expect(isValidVariableName('mood')).toBe(true);
		expect(isValidVariableName('_mood2')).toBe(true);
		expect(isValidVariableName('Mood_2')).toBe(true);
	});

	it('rejects names starting with a digit, with spaces, or empty', () => {
		expect(isValidVariableName('2mood')).toBe(false);
		expect(isValidVariableName('my mood')).toBe(false);
		expect(isValidVariableName('')).toBe(false);
	});
});

describe('variableUsageSyntax', () => {
	it('produces exactly ${name}', () => {
		expect(variableUsageSyntax('mood')).toBe('${mood}');
	});
});

describe('detectVariableTrigger', () => {
	it('detects a $ trigger with no query yet', () => {
		const match = detectVariableTrigger('a photo of $', 12);
		expect(match).toEqual({ start: 11, end: 12, query: '' });
	});

	it('detects a $ trigger with a partial query', () => {
		const text = 'a photo of $mo';
		const match = detectVariableTrigger(text, text.length);
		expect(match).toEqual({ start: 11, end: 14, query: 'mo' });
	});

	it('returns null when there is no $ on the line', () => {
		expect(detectVariableTrigger('a plain prompt', 5)).toBeNull();
	});

	it('stops scanning at a space, so a completed word does not falsely trigger', () => {
		const text = '$mood is nice';
		expect(detectVariableTrigger(text, text.length)).toBeNull();
	});

	it('stops scanning at a { so it does not reach into a choice group', () => {
		const text = '{a|b} $mo';
		const match = detectVariableTrigger(text, text.length);
		expect(match).toEqual({ start: 6, end: 9, query: 'mo' });
	});
});

describe('filterVariableNames', () => {
	const names = ['mood', 'model', 'season', 'MoonPhase'];

	it('ranks prefix matches before substring matches', () => {
		// All four names start with "mo", so this is really just verifying the
		// (locale, case-insensitive) alphabetical tiebreak within the prefix tier.
		expect(filterVariableNames(names, 'mo')).toEqual(['model', 'mood', 'MoonPhase']);
	});

	it('puts a prefix match ahead of a same-letters substring match', () => {
		expect(filterVariableNames(['xmood', 'mood'], 'moo')).toEqual(['mood', 'xmood']);
	});

	it('is case-insensitive', () => {
		expect(filterVariableNames(names, 'SEA')).toEqual(['season']);
	});

	it('returns everything, sorted, for an empty query', () => {
		expect(filterVariableNames(['b', 'a', 'c'], '')).toEqual(['a', 'b', 'c']);
	});
});

describe('insertVariableUsage', () => {
	it('replaces the trigger span with ${name}, leaving the rest of the text untouched', () => {
		const text = 'a photo of $mo, warm light';
		const trigger = detectVariableTrigger(text, 14)!;
		const result = insertVariableUsage(text, trigger, 'mood');
		expect(result).toBe('a photo of ${mood}, warm light');
	});
});

describe('extractVariableUsages', () => {
	it('finds a single usage', () => {
		expect(extractVariableUsages('a photo in a ${mood} style')).toEqual(['mood']);
	});

	it('dedupes repeated usages, keeping first-seen order', () => {
		expect(extractVariableUsages('${mood} lighting, ${mood} tone, ${era}')).toEqual(['mood', 'era']);
	});

	it('returns an empty array when there are no usages', () => {
		expect(extractVariableUsages('a plain prompt')).toEqual([]);
	});

	it('ignores an unrelated $ that is not a ${name} usage', () => {
		expect(extractVariableUsages('$5 bill, {a|b}')).toEqual([]);
	});
});

describe('findUndefinedVariableUsages', () => {
	it('flags a usage with no matching definition', () => {
		expect(findUndefinedVariableUsages(['a ${mood} photo'], [])).toEqual(['mood']);
	});

	it('does not flag a defined variable', () => {
		expect(findUndefinedVariableUsages(['a ${mood} photo'], ['mood'])).toEqual([]);
	});

	it('scans every text in the list and dedupes across them', () => {
		const texts = ['${mood} photo', 'negative: ${mood}, ${era}'];
		expect(findUndefinedVariableUsages(texts, [])).toEqual(['mood', 'era']);
	});

	it('only reports the names that are actually missing, not the defined ones', () => {
		const texts = ['${mood} and ${era} style'];
		expect(findUndefinedVariableUsages(texts, ['mood'])).toEqual(['era']);
	});

	it('returns an empty array when nothing is undefined', () => {
		expect(findUndefinedVariableUsages(['no variables here'], ['mood'])).toEqual([]);
	});
});

describe('parseVariableUsageTokens', () => {
	it('returns a single text token for plain text', () => {
		expect(parseVariableUsageTokens('a plain prompt')).toEqual([
			{ type: 'text', raw: 'a plain prompt', start: 0, end: 14 }
		]);
	});

	it('extracts one usage with surrounding text spans, exact offsets', () => {
		const text = 'a photo, ${mood} lighting';
		const tokens = parseVariableUsageTokens(text);
		expect(tokens).toEqual([
			{ type: 'text', raw: 'a photo, ', start: 0, end: 9 },
			{ type: 'variable', raw: '${mood}', start: 9, end: 16, name: 'mood' },
			{ type: 'text', raw: ' lighting', start: 16, end: 25 }
		]);
	});

	it('handles back-to-back usages with no separating text', () => {
		const tokens = parseVariableUsageTokens('${a}${b}');
		expect(tokens.map((t) => t.type)).toEqual(['variable', 'variable']);
		expect(tokens.map((t) => (t.type === 'variable' ? t.name : null))).toEqual(['a', 'b']);
	});

	it('round-trips: raw spans reconstruct the source exactly', () => {
		const text = 'before ${x} middle ${y} after';
		expect(
			parseVariableUsageTokens(text)
				.map((t) => t.raw)
				.join('')
		).toBe(text);
	});

	it('ignores an unterminated ${ (no matching })', () => {
		const tokens = parseVariableUsageTokens('broken ${oops');
		expect(tokens).toEqual([{ type: 'text', raw: 'broken ${oops', start: 0, end: 13 }]);
	});
});

describe('countVariableUsages', () => {
	it('counts zero for plain text', () => {
		expect(countVariableUsages('nothing here')).toBe(0);
	});

	it('counts occurrences, not unique names', () => {
		expect(countVariableUsages('${mood} and ${mood} again')).toBe(2);
	});

	it('does not count an unterminated ${', () => {
		expect(countVariableUsages('typing ${mo')).toBe(0);
	});

	it('counts multiple distinct variables', () => {
		expect(countVariableUsages('${a} ${b} ${c}')).toBe(3);
	});
});
