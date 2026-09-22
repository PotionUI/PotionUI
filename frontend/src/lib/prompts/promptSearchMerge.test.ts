import { describe, it, expect } from 'vitest';
import { mergePromptSearchResults } from './promptSearchMerge';
import type { Prompt } from '$lib/types/segments';

function prompt(id: string): Prompt {
	return { id, display_name: id, segments: [], flattened_text: id };
}

describe('mergePromptSearchResults', () => {
	it('puts semantic hits first, then plain results, deduped by id', () => {
		const semantic = [prompt('b'), prompt('a')];
		const plain = [prompt('a'), prompt('c')];

		const merged = mergePromptSearchResults(semantic, plain);

		expect(merged.map((p) => p.id)).toEqual(['b', 'a', 'c']);
	});

	it('returns the plain results unchanged when there are no semantic hits', () => {
		const plain = [prompt('a'), prompt('b')];
		expect(mergePromptSearchResults([], plain).map((p) => p.id)).toEqual(['a', 'b']);
	});

	it('returns an empty list when both inputs are empty', () => {
		expect(mergePromptSearchResults([], [])).toEqual([]);
	});
});
