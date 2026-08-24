import { describe, it, expect } from 'vitest';
import type { ChipData } from '$lib/types/segments';
import {
	encodePathForText,
	decodePathFromMatch,
	splitPromptTokensInText,
	parseChipSegments,
	parseValueToSegments
} from './chipSegments';

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

describe('encodePathForText', () => {
	it('encodes a plain path with no spaces as #path', () => {
		expect(encodePathForText('emotions.happy')).toBe('#emotions.happy');
	});

	it('encodes a path containing a space in bracket form', () => {
		expect(encodePathForText('emotions.very happy')).toBe('#[emotions.very happy]');
	});

	it('bracket-encodes even a single leading/trailing space', () => {
		expect(encodePathForText(' leading')).toBe('#[ leading]');
	});
});

describe('decodePathFromMatch', () => {
	const chipPattern = /#\[([^\]]+)\]|#([\w][\w.]*)/g;

	it('prefers the bracketed group when both could match', () => {
		const match = chipPattern.exec('#[a b]')!;
		expect(decodePathFromMatch(match)).toBe('a b');
	});

	it('falls back to the simple group for a plain path', () => {
		chipPattern.lastIndex = 0;
		const match = chipPattern.exec('#simple.path')!;
		expect(decodePathFromMatch(match)).toBe('simple.path');
	});
});

describe('splitPromptTokensInText', () => {
	it('returns one text segment for plain text (no group/variable tokens)', () => {
		expect(splitPromptTokensInText('just plain text')).toEqual([
			{ type: 'text', content: 'just plain text' }
		]);
	});

	it('splits out a choice group as its own segment', () => {
		const segs = splitPromptTokensInText('a photo of {cat|dog}');
		expect(segs).toEqual([
			{ type: 'text', content: 'a photo of ' },
			{ type: 'group', content: '{cat|dog}', groupRaw: '{cat|dog}' }
		]);
	});

	it('splits out a variable usage as its own segment, carrying variableName', () => {
		const segs = splitPromptTokensInText('mood: ${mood}');
		expect(segs).toEqual([
			{ type: 'text', content: 'mood: ' },
			{ type: 'variable', content: '${mood}', variableRaw: '${mood}', variableName: 'mood' }
		]);
	});

	it('drops zero-length text runs between adjacent tokens', () => {
		const segs = splitPromptTokensInText('{a|b}${x}');
		expect(segs.map((s) => s.type)).toEqual(['group', 'variable']);
	});
});

describe('parseChipSegments', () => {
	it('returns empty array for empty text', () => {
		expect(parseChipSegments('', {})).toEqual([]);
	});

	it('replaces a matching #path marker with a chip segment', () => {
		const chip = makeChip();
		const segs = parseChipSegments('a #emotions.happy face', { [chip.id]: chip });
		expect(segs).toEqual([
			{ type: 'text', content: 'a ' },
			{ type: 'chip', content: '#emotions.happy', chipId: 'chip-1', chipData: chip },
			{ type: 'text', content: ' face' }
		]);
	});

	it('leaves an unmatched #path marker as its own text segment (not merged with surrounding text)', () => {
		const segs = parseChipSegments('a #nonexistent.path face', {});
		expect(segs).toEqual([
			{ type: 'text', content: 'a ' },
			{ type: 'text', content: '#nonexistent.path' },
			{ type: 'text', content: ' face' }
		]);
	});

	it('resolves a bracketed #[path with spaces] marker', () => {
		const chip = makeChip({ id: 'chip-2', categoryPath: 'emotions.very happy' });
		const segs = parseChipSegments('#[emotions.very happy]', { [chip.id]: chip });
		expect(segs).toEqual([{ type: 'chip', content: '#[emotions.very happy]', chipId: 'chip-2', chipData: chip }]);
	});

	it('does not double-assign the same chip id to two markers with the same categoryPath', () => {
		const chip = makeChip();
		const segs = parseChipSegments('#emotions.happy and #emotions.happy', { [chip.id]: chip });
		expect(segs[0]).toMatchObject({ type: 'chip', chipId: 'chip-1' });
		// Second occurrence: same categoryPath, but the only matching chip id is
		// already used — falls back to plain text instead of resolving.
		expect(segs[2]).toEqual({ type: 'text', content: '#emotions.happy' });
	});

	it('resolves distinct chip ids sharing the same categoryPath to separate markers in order', () => {
		const chipA = makeChip({ id: 'chip-a' });
		const chipB = makeChip({ id: 'chip-b' });
		const segs = parseChipSegments('#emotions.happy #emotions.happy', {
			'chip-a': chipA,
			'chip-b': chipB
		});
		const chipSegs = segs.filter((s) => s.type === 'chip');
		expect(chipSegs).toHaveLength(2);
		expect(new Set(chipSegs.map((s) => s.chipId))).toEqual(new Set(['chip-a', 'chip-b']));
	});
});

describe('parseValueToSegments (full pipeline: #chips -> {group}/${variable})', () => {
	it('returns empty array for empty text', () => {
		expect(parseValueToSegments('', {})).toEqual([]);
	});

	it('produces plain text unchanged when no tokens are present', () => {
		expect(parseValueToSegments('just a prompt', {})).toEqual([{ type: 'text', content: 'just a prompt' }]);
	});

	it('resolves a #chip and splits the remaining text for groups/variables in one pass', () => {
		const chip = makeChip();
		const segs = parseValueToSegments('#emotions.happy face, {red|blue} eyes, ${mood} mood', {
			[chip.id]: chip
		});
		expect(segs.map((s) => s.type)).toEqual(['chip', 'text', 'group', 'text', 'variable', 'text']);
		expect(segs[0]).toMatchObject({ type: 'chip', chipId: 'chip-1' });
		expect(segs[2]).toMatchObject({ type: 'group', groupRaw: '{red|blue}' });
		expect(segs[4]).toMatchObject({ type: 'variable', variableRaw: '${mood}', variableName: 'mood' });
	});

	it('leaves a ${name} embedded inside a group option opaque (group parsing wins over #chip splitting on that span)', () => {
		const segs = parseValueToSegments('{a|${mood} b}', {});
		expect(segs).toEqual([{ type: 'group', content: '{a|${mood} b}', groupRaw: '{a|${mood} b}' }]);
	});

	it('a #path marker embedded before a group is still resolved independently of the group split', () => {
		const chip = makeChip();
		const segs = parseValueToSegments('#emotions.happy {a|b}', { [chip.id]: chip });
		expect(segs.map((s) => s.type)).toEqual(['chip', 'text', 'group']);
	});
});
