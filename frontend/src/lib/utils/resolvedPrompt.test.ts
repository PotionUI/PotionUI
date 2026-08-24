import { describe, expect, it } from 'vitest';
import type { ChipData, Segment } from '$lib/types/segments';
import { flattenRichSegments } from './richSegments';
import { resolvedPromptStats, resolvedPromptTokens } from './resolvedPrompt';

function editor(id: string, content: string, partial: Partial<Segment> = {}): Segment {
	return { id, content, type: 'content', chips: {}, enabled: true, ...partial };
}

function chip(value: string): ChipData {
	return {
		id: 'c1',
		categoryPath: 'people.role',
		valueId: 'v1',
		label: value,
		value,
		allValues: [],
		shuffle: false,
		autoRegen: false
	};
}

describe('resolvedPromptStats', () => {
	it('counts the characters the model receives, not the characters on screen', () => {
		const segments = [editor('a', 'a forest'), editor('b', 'blurry', { enabled: false })];

		expect(resolvedPromptStats(segments).chars).toBe('a forest'.length);
		expect(resolvedPromptStats(segments).chars).toBe(flattenRichSegments(segments).length);
	});

	it('includes the joining punctuation between segments', () => {
		const segments = [editor('a', 'a forest'), editor('b', 'at dusk')];
		expect(resolvedPromptStats(segments).chars).toBe('a forest, at dusk'.length);
	});

	it('counts enabled breaks', () => {
		const segments = [editor('a', 'x'), editor('b', '', { type: 'break' }), editor('c', 'y')];
		expect(resolvedPromptStats(segments).breaks).toBe(1);
	});

	it('does not count a disabled break', () => {
		const segments = [
			editor('a', 'x'),
			editor('b', '', { type: 'break', enabled: false }),
			editor('c', 'y')
		];
		expect(resolvedPromptStats(segments).breaks).toBe(0);
		expect(resolvedPromptStats(segments).chars).toBe('x, y'.length);
	});

	it('is zero for an empty list and for a blank placeholder card', () => {
		expect(resolvedPromptStats([])).toEqual({ chars: 0, breaks: 0 });
		expect(resolvedPromptStats([editor('a', '')])).toEqual({ chars: 0, breaks: 0 });
	});
});

describe('resolvedPromptTokens', () => {
	function joined(segments: Segment[]): string {
		return resolvedPromptTokens(segments)
			.map((token) => token.text)
			.join('');
	}

	it('reproduces the resolved string exactly when concatenated', () => {
		const segments = [
			editor('a', 'cinematic portrait, (35mm anamorphic:1.2)'),
			editor('b', '', { type: 'break' }),
			editor('c', 'shallow depth of field, [oversaturated]')
		];
		expect(joined(segments)).toBe(flattenRichSegments(segments));
	});

	it('never emits a token for a disabled segment', () => {
		const segments = [editor('a', 'a forest'), editor('b', 'harsh noon sun', { enabled: false })];
		expect(joined(segments)).toBe('a forest');
		expect(resolvedPromptTokens(segments).every((token) => !token.text.includes('harsh'))).toBe(true);
	});

	it('tags BREAK as its own token, not as body text', () => {
		const segments = [editor('a', 'x'), editor('b', '', { type: 'break' }), editor('c', 'y')];
		const breaks = resolvedPromptTokens(segments).filter((token) => token.kind === 'break');
		expect(breaks).toEqual([{ kind: 'break', text: 'BREAK' }]);
	});

	it('does not tag BREAK inside a longer word', () => {
		const segments = [editor('a', 'BREAKWATER at dawn')];
		expect(resolvedPromptTokens(segments).some((token) => token.kind === 'break')).toBe(false);
	});

	it('tags attention syntax as emphasis and bracketed text as muted', () => {
		const segments = [editor('a', 'a (35mm anamorphic:1.2) lens, [oversaturated]')];
		const tokens = resolvedPromptTokens(segments);

		expect(tokens).toContainEqual({ kind: 'emphasis', text: '(35mm anamorphic:1.2)' });
		expect(tokens).toContainEqual({ kind: 'muted', text: '[oversaturated]' });
	});

	it('tags a chip value as a substitution so it reads at full strength', () => {
		const segments = [
			editor('a', 'cinematic portrait of a lighthouse keeper, weathered face', {
				chips: { c1: chip('lighthouse keeper') }
			})
		];
		expect(resolvedPromptTokens(segments)).toContainEqual({
			kind: 'value',
			text: 'lighthouse keeper'
		});
	});

	it('does not light up a chip value found inside a longer word', () => {
		const segments = [editor('a', 'a sunset over water', { chips: { c1: chip('sun') } })];
		const values = resolvedPromptTokens(segments).filter((token) => token.kind === 'value');
		expect(values).toEqual([]);
	});

	it('tags a ${variable} marker as a substitution', () => {
		const segments = [editor('a', 'shot in ${style} style')];
		expect(resolvedPromptTokens(segments)).toContainEqual({ kind: 'value', text: '${style}' });
	});

	it('lets the outer attention span win over a chip value nested inside it', () => {
		// The chip value is free-standing here — "(" before, ")" after — so it is a
		// genuine candidate and only the overlap rule can suppress it.
		const segments = [
			editor('a', 'a (lighthouse keeper) portrait', { chips: { c1: chip('lighthouse keeper') } })
		];
		const tokens = resolvedPromptTokens(segments);

		expect(tokens).toContainEqual({ kind: 'emphasis', text: '(lighthouse keeper)' });
		expect(tokens.some((token) => token.kind === 'value')).toBe(false);
		expect(joined(segments)).toBe('a (lighthouse keeper) portrait');
	});

	it('is empty for a prompt with no enabled content', () => {
		expect(resolvedPromptTokens([])).toEqual([]);
		expect(resolvedPromptTokens([editor('a', '', { enabled: false })])).toEqual([]);
	});
});
