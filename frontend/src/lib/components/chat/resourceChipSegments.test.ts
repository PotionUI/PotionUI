import { describe, it, expect } from 'vitest';
import type { ResourceChipData } from '$lib/types/chat';
import { markerRegex, parseValueToSegments } from './resourceChipSegments';

function makeResource(overrides: Partial<ResourceChipData> = {}): ResourceChipData {
	return { uri: 'lora.abc', label: 'A LoRA', ...overrides };
}

describe('markerRegex', () => {
	it('matches a simple @path marker', () => {
		const matches = 'hello @lora.abc there'.match(markerRegex());
		expect(matches).toEqual(['@lora.abc']);
	});

	it('matches a bracketed @[path with spaces] marker', () => {
		const matches = 'hello @[a b] there'.match(markerRegex());
		expect(matches).toEqual(['@[a b]']);
	});

	// Divergence from InlineChipEditor's #chip pattern (chipSegments.ts): the
	// resource marker excludes a boundary immediately preceded by a word char
	// or another @, specifically to avoid matching inside "user@host.com".
	it('does not match an @ immediately preceded by a word character (e.g. an email-shaped run)', () => {
		expect('user@host.com'.match(markerRegex())).toBeNull();
	});

	it('does not match at all inside a run of two @s (the second @ is also excluded by the lookbehind, not just the first)', () => {
		expect('@@double'.match(markerRegex())).toBeNull();
	});

	// Divergence: resource paths allow '-' in the simple (unbracketed) form;
	// chipSegments.ts's #chip pattern does not.
	it('allows a hyphen in the simple (unbracketed) path form', () => {
		expect('@a-b-c'.match(markerRegex())).toEqual(['@a-b-c']);
	});

	it('a fresh call returns a fresh RegExp instance each time (no shared lastIndex state)', () => {
		expect(markerRegex()).not.toBe(markerRegex());
	});
});

describe('parseValueToSegments', () => {
	it('returns empty array for empty text', () => {
		expect(parseValueToSegments('', {})).toEqual([]);
	});

	it('resolves a matching @path marker to a chip segment', () => {
		const res = makeResource();
		const segs = parseValueToSegments('see @lora.abc here', { r1: res });
		expect(segs).toEqual([
			{ type: 'text', content: 'see ' },
			{ type: 'chip', content: '@lora.abc', chipId: 'r1', chipData: res },
			{ type: 'text', content: ' here' }
		]);
	});

	it('leaves an unmatched @path marker as its own text segment', () => {
		const segs = parseValueToSegments('see @nonexistent here', {});
		expect(segs).toEqual([
			{ type: 'text', content: 'see ' },
			{ type: 'text', content: '@nonexistent' },
			{ type: 'text', content: ' here' }
		]);
	});

	it('resolves a bracketed @[uri with spaces] marker by exact uri match', () => {
		const res = makeResource({ uri: 'a b' });
		const segs = parseValueToSegments('@[a b]', { r1: res });
		expect(segs).toEqual([{ type: 'chip', content: '@[a b]', chipId: 'r1', chipData: res }]);
	});

	it('does not double-assign the same resource id to two markers with the same uri', () => {
		const res = makeResource();
		const segs = parseValueToSegments('@lora.abc and @lora.abc', { r1: res });
		expect(segs[0]).toMatchObject({ type: 'chip', chipId: 'r1' });
		expect(segs[2]).toEqual({ type: 'text', content: '@lora.abc' });
	});

	// Divergence from InlineChipEditor.svelte's parseValueToSegments: this
	// version is the #chip pass ONLY — there is no second pass for
	// {a|b}-style choice groups or ${name} variable usages. A literal
	// "{a|b}" in a chat message is left as plain text.
	it('does not split out {a|b}-style choice-group syntax (ChatChipInput has no group/variable pass)', () => {
		const segs = parseValueToSegments('roll a {a|b} please', {});
		expect(segs).toEqual([{ type: 'text', content: 'roll a {a|b} please' }]);
	});
});
