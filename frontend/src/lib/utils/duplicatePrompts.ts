import type { Prompt } from '$lib/types/segments';

export interface DuplicateGroup {
	similarity: number;
	prompts: Prompt[];
}

/** Similarity presets exposed in the duplicates UI, mapped to the backend's cosine-distance threshold. */
export const DUPLICATE_THRESHOLD_PRESETS: Array<{ label: string; value: number }> = [
	{ label: 'Strict', value: 0.05 },
	{ label: 'Normal', value: 0.1 },
	{ label: 'Loose', value: 0.25 }
];

/**
 * Remove the given prompt ids from one duplicate group. A group that drops
 * below two members is no longer a duplicate and is dropped entirely.
 */
export function removePromptsFromDuplicateGroup(
	groups: DuplicateGroup[],
	groupIndex: number,
	removedIds: string[]
): DuplicateGroup[] {
	const group = groups[groupIndex];
	if (!group) return groups;

	const removedSet = new Set(removedIds);
	const remainingPrompts = group.prompts.filter((prompt) => !removedSet.has(prompt.id));
	const next = [...groups];
	if (remainingPrompts.length < 2) {
		next.splice(groupIndex, 1);
	} else {
		next[groupIndex] = { ...group, prompts: remainingPrompts };
	}
	return next;
}
