import type { Prompt } from '$lib/types/segments';

export function mergePromptSearchResults(semanticHits: Prompt[], plainResults: Prompt[]): Prompt[] {
	const seen = new Set<string>();
	const merged: Prompt[] = [];
	for (const prompt of semanticHits) {
		if (seen.has(prompt.id)) continue;
		seen.add(prompt.id);
		merged.push(prompt);
	}
	for (const prompt of plainResults) {
		if (seen.has(prompt.id)) continue;
		seen.add(prompt.id);
		merged.push(prompt);
	}
	return merged;
}
