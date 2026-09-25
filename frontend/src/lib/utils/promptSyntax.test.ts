import { describe, expect, it } from 'vitest';
import {
	applySyntaxInsert,
	buildSyntaxSegments,
	detectSyntaxPickerTrigger,
	filterSyntaxSpecs,
	findSyntaxMatches,
	parseWeightNumber,
	sanitizeSyntaxColor,
	syntaxColorStyle,
	syntaxToneClasses,
	weightTone,
	type PromptSyntaxSpec
} from './promptSyntax';

const speakerSpec: PromptSyntaxSpec = {
	token: '(S1)',
	kind: 'marker',
	pattern: '\\(S\\d+\\)',
	help: 'Speaker reference',
	tone: 'accent'
};

const dialogueSpec: PromptSyntaxSpec = {
	token: '<d></d>',
	kind: 'wrap',
	pattern: '<d>[\\s\\S]*?</d>',
	insert: '<d>{}</d>',
	help: 'Exact words'
};

const breakSpec: PromptSyntaxSpec = {
	token: 'BREAK',
	kind: 'marker',
	help: 'Splits into CLIP chunks'
};

const weightSpec: PromptSyntaxSpec = {
	token: '(tag:1.2)',
	kind: 'weight',
	pattern: '\\(([^()]+?):(\\d+(?:[.,]\\d+)?)\\)',
	insert: '({}:{weight=1.2})',
	help: 'Explicit attention weight'
};

const labelSpec: PromptSyntaxSpec = {
	token: 'non_diegetic_music:',
	kind: 'label',
	help: 'Background score'
};

describe('parseWeightNumber', () => {
	it('parses plain decimals', () => {
		expect(parseWeightNumber('1.2')).toBe(1.2);
	});

	it('parses comma decimals', () => {
		expect(parseWeightNumber('1,3')).toBe(1.3);
	});

	it('parses integers', () => {
		expect(parseWeightNumber('1')).toBe(1);
	});

	it('returns null for garbage', () => {
		expect(parseWeightNumber('abc')).toBeNull();
		expect(parseWeightNumber('')).toBeNull();
	});
});

describe('weightTone', () => {
	it('is warning above 1', () => {
		expect(weightTone(1.2)).toBe('warning');
	});

	it('is info below 1', () => {
		expect(weightTone(0.8)).toBe('info');
	});

	it('is muted at exactly 1', () => {
		expect(weightTone(1)).toBe('muted');
	});
});

describe('findSyntaxMatches', () => {
	it('matches a marker pattern and keeps its tone', () => {
		const matches = findSyntaxMatches('(S1) says hello, (S2) replies.', [speakerSpec]);
		expect(matches).toHaveLength(2);
		expect(matches[0]).toMatchObject({ start: 0, end: 4, text: '(S1)', tone: 'accent' });
		expect(matches[1]).toMatchObject({ start: 17, end: 21, text: '(S2)' });
	});

	it('matches a label token with no explicit pattern via the escaped token', () => {
		const matches = findSyntaxMatches('non_diegetic_music: strings, slow tempo', [labelSpec]);
		expect(matches).toHaveLength(1);
		expect(matches[0].tone).toBe('info');
	});

	it('matches a wrap pattern spanning multiple lines, stopping at the closing tag', () => {
		const multilineDialogue: PromptSyntaxSpec = {
			token: '<d></d>',
			kind: 'wrap',
			pattern: '<d>[\\s\\S]*?</d>',
			insert: '<d>{}</d>',
			help: "Wraps a speaker's exact words, preserved verbatim"
		};
		const text = '(S1) says: <d>I cry\nI scream\n...\nUnderneath</d>.';
		const matches = findSyntaxMatches(text, [speakerSpec, multilineDialogue]);
		const dialogueMatch = matches.find((m) => m.spec === multilineDialogue);
		expect(dialogueMatch?.text).toBe('<d>I cry\nI scream\n...\nUnderneath</d>');
		expect(dialogueMatch?.text).not.toContain('</d>.');
	});

	it('matches a wrap pattern spanning the whole tag', () => {
		const matches = findSyntaxMatches('(S1) says: <d>[English] hi there</d>', [speakerSpec, dialogueSpec]);
		const wrap = matches.find((m) => m.spec === dialogueSpec);
		expect(wrap?.text).toBe('<d>[English] hi there</d>');
		expect(wrap?.tone).toBe('signal');
	});

	it('derives weight tone from the matched value, above/below/at 1', () => {
		const text = '(sharp:1.4) (soft:0.6) (tag:1,3) (flat:1)';
		const matches = findSyntaxMatches(text, [weightSpec]);
		expect(matches.map((m) => m.tone)).toEqual(['warning', 'info', 'warning', 'muted']);
	});

	it('drops fully-overlapping matches, keeping the earliest/longest', () => {
		const matches = findSyntaxMatches('BREAK', [breakSpec, { ...breakSpec, token: 'BR', pattern: 'BR' }]);
		expect(matches).toHaveLength(1);
		expect(matches[0].text).toBe('BREAK');
	});

	it('returns nothing for empty text or no specs', () => {
		expect(findSyntaxMatches('', [speakerSpec])).toEqual([]);
		expect(findSyntaxMatches('(S1)', [])).toEqual([]);
	});
});

describe('applySyntaxInsert', () => {
	it('inserts a plain marker unchanged', () => {
		const result = applySyntaxInsert('(S1)', '');
		expect(result).toEqual({ text: '(S1)', caretOffset: 4, selectionStart: null, selectionEnd: null });
	});

	it('places the caret inside an empty {} placeholder', () => {
		const result = applySyntaxInsert('<d>{}</d>', '');
		expect(result.text).toBe('<d></d>');
		expect(result.caretOffset).toBe(3);
		expect(result.selectionStart).toBeNull();
	});

	it('wraps a selection inside {}', () => {
		const result = applySyntaxInsert('<d>{}</d>', 'hello there');
		expect(result.text).toBe('<d>hello there</d>');
	});

	it('reports the named param as a selectable range for overtype', () => {
		const result = applySyntaxInsert('({}:{weight=1.2})', '');
		expect(result.text).toBe('(:1.2)');
		expect(result.selectionStart).toBe(2);
		expect(result.selectionEnd).toBe(5);
		expect(result.caretOffset).toBe(5);
	});

	it('wraps a selection AND selects the weight param', () => {
		const result = applySyntaxInsert('({}:{weight=1.2})', 'masterpiece');
		expect(result.text).toBe('(masterpiece:1.2)');
		expect(result.selectionStart).toBe(13);
		expect(result.selectionEnd).toBe(16);
	});
});

describe('buildSyntaxSegments', () => {
	it('returns nothing for empty text', () => {
		expect(buildSyntaxSegments('', [speakerSpec])).toEqual([]);
	});

	it('returns one plain segment when nothing matches', () => {
		expect(buildSyntaxSegments('plain text', [speakerSpec])).toEqual([{ text: 'plain text', match: null }]);
	});

	it('splits text around matches, preserving surrounding plain runs', () => {
		const segments = buildSyntaxSegments('(S1) says hi', [speakerSpec]);
		expect(segments.map((s) => s.text)).toEqual(['(S1)', ' says hi']);
		expect(segments[0].match?.spec).toBe(speakerSpec);
		expect(segments[1].match).toBeNull();
	});

	it('handles a match in the middle with text on both sides', () => {
		const segments = buildSyntaxSegments('before (S1) after', [speakerSpec]);
		expect(segments.map((s) => s.text)).toEqual(['before ', '(S1)', ' after']);
		expect(segments.map((s) => s.match !== null)).toEqual([false, true, false]);
	});
});

describe('detectSyntaxPickerTrigger', () => {
	it('detects a bare / at the start of the text', () => {
		expect(detectSyntaxPickerTrigger('/', 1)).toEqual({ start: 0, end: 1, query: '' });
	});

	it('detects / preceded by whitespace with a partial query', () => {
		expect(detectSyntaxPickerTrigger('hello /bre', 10)).toEqual({ start: 6, end: 10, query: 'bre' });
	});

	it('does not trigger inside a URL (/ preceded by a non-space character)', () => {
		const text = 'see https://example.com/path';
		expect(detectSyntaxPickerTrigger(text, text.length)).toBeNull();
	});

	it('does not trigger when there is no / before the cursor', () => {
		expect(detectSyntaxPickerTrigger('plain text', 5)).toBeNull();
	});

	it('does not trigger across a newline', () => {
		expect(detectSyntaxPickerTrigger('/break\nBREAK', 12)).toBeNull();
	});

	it('rejects a query with punctuation that is not word/space/dot/hyphen', () => {
		expect(detectSyntaxPickerTrigger('/bre@k', 6)).toBeNull();
	});
});

describe('syntaxToneClasses', () => {
	it('returns semantic-token classes for every tone, never a raw color', () => {
		const tones: Array<Parameters<typeof syntaxToneClasses>[0]> = [
			'signal',
			'success',
			'warning',
			'info',
			'accent',
			'danger',
			'muted'
		];
		for (const tone of tones) {
			const classes = syntaxToneClasses(tone);
			expect(classes).not.toMatch(/zinc|gray|#/);
			expect(classes.length).toBeGreaterThan(0);
		}
	});
});

describe('sanitizeSyntaxColor', () => {
	it('accepts hex colors in every declared shape and lowercases them', () => {
		expect(sanitizeSyntaxColor('#ABC')).toBe('#abc');
		expect(sanitizeSyntaxColor('#AABBCC')).toBe('#aabbcc');
		expect(sanitizeSyntaxColor('#AABBCCDD')).toBe('#aabbccdd');
	});

	it('accepts a lowercase CSS named color as-is', () => {
		expect(sanitizeSyntaxColor('cornflowerblue')).toBe('cornflowerblue');
	});

	it('rejects a mixed-case named color', () => {
		expect(sanitizeSyntaxColor('CornflowerBlue')).toBeNull();
	});

	it('rejects anything that is not a plain hex or letters-only string', () => {
		expect(sanitizeSyntaxColor('red; } body { display: none')).toBeNull();
		expect(sanitizeSyntaxColor('rgb(0,0,0)')).toBeNull();
		expect(sanitizeSyntaxColor('#gggggg')).toBeNull();
		expect(sanitizeSyntaxColor('')).toBeNull();
		expect(sanitizeSyntaxColor(null)).toBeNull();
		expect(sanitizeSyntaxColor(undefined)).toBeNull();
	});
});

describe('syntaxColorStyle', () => {
	it('builds a background+color declaration for a valid color', () => {
		const style = syntaxColorStyle('coral');
		expect(style).toBe('background-color: color-mix(in srgb, coral 16%, transparent); color: coral;');
	});

	it('returns null for an unsanitizable color', () => {
		expect(syntaxColorStyle('not a color!')).toBeNull();
	});
});

describe('findSyntaxMatches color resolution', () => {
	const speakerWithColor: PromptSyntaxSpec = {
		token: '(S1)',
		kind: 'marker',
		pattern: '\\(S\\d+\\)',
		color: 'mediumorchid'
	};

	it('resolves the spec color onto the match, leaving tone as the kind default', () => {
		const [match] = findSyntaxMatches('(S1) says hi', [speakerWithColor]);
		expect(match.color).toBe('mediumorchid');
		expect(match.tone).toBe('signal');
	});

	it('resolves color to null when the spec has none', () => {
		const [match] = findSyntaxMatches('(S1) says hi', [speakerSpec]);
		expect(match.color).toBeNull();
	});

	it('never carries a color for a weight kind, even if one were declared', () => {
		const weightWithColor: PromptSyntaxSpec = { ...weightSpec, color: 'coral' };
		const matches = findSyntaxMatches('(tag:1.2)', [weightWithColor]);
		expect(matches[0].color).toBeNull();
		expect(matches[0].tone).toBe('warning');
	});
});

describe('filterSyntaxSpecs', () => {
	const specs = [speakerSpec, dialogueSpec, breakSpec, labelSpec];

	it('returns everything for an empty query', () => {
		expect(filterSyntaxSpecs(specs, '')).toEqual(specs);
	});

	it('matches by token, case-insensitively', () => {
		expect(filterSyntaxSpecs(specs, 'break')).toEqual([breakSpec]);
	});

	it('matches by help text', () => {
		expect(filterSyntaxSpecs(specs, 'score')).toEqual([labelSpec]);
	});

	it('returns nothing when nothing matches', () => {
		expect(filterSyntaxSpecs(specs, 'zzz')).toEqual([]);
	});
});
