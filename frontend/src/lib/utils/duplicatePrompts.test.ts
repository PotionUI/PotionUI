import { describe, expect, it } from 'vitest';
import { removePromptsFromDuplicateGroup, type DuplicateGroup } from './duplicatePrompts';
import type { Prompt } from '$lib/types/segments';

function makePrompt(id: string): Prompt {
	return { id, display_name: id, segments: [], flattened_text: id } as Prompt;
}

function makeGroups(): DuplicateGroup[] {
	return [
		{ similarity: 0.98, prompts: [makePrompt('a'), makePrompt('b'), makePrompt('c')] },
		{ similarity: 1, prompts: [makePrompt('x'), makePrompt('y')] }
	];
}

describe('removePromptsFromDuplicateGroup', () => {
	it('drops the removed prompts but keeps the group when 2+ remain', () => {
		const groups = makeGroups();
		const result = removePromptsFromDuplicateGroup(groups, 0, ['b']);

		expect(result).toHaveLength(2);
		expect(result[0].prompts.map((prompt) => prompt.id)).toEqual(['a', 'c']);
		expect(result[0].similarity).toBe(0.98);
		// Untouched groups are left as-is (same reference).
		expect(result[1]).toBe(groups[1]);
	});

	it('drops the whole group once fewer than two prompts remain', () => {
		const result = removePromptsFromDuplicateGroup(makeGroups(), 1, ['x']);

		expect(result).toHaveLength(1);
		expect(result[0].prompts.map((prompt) => prompt.id)).toEqual(['a', 'b', 'c']);
	});

	it('drops the whole group when every prompt in it is removed at once', () => {
		const result = removePromptsFromDuplicateGroup(makeGroups(), 0, ['a', 'b', 'c']);

		expect(result).toHaveLength(1);
		expect(result[0].prompts.map((prompt) => prompt.id)).toEqual(['x', 'y']);
	});

	it('is a no-op for an out-of-range group index', () => {
		const groups = makeGroups();
		expect(removePromptsFromDuplicateGroup(groups, 5, ['a'])).toBe(groups);
	});
});
