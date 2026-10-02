import type { Tab } from '$lib/types/tabs';
import { rewriteMergedMarkers } from '$lib/form/mergeFrom';

export const PROMPT_STATE_KEYS = [
	'prompt',
	'negativePrompt',
	'promptSegments',
	'negativePromptSegments',
	'promptTabs',
	'promptRelay',
	'videoDirector',
	'musicDirector'
] as const;

export function tabMergedMarkerUpdates(tab: Partial<Tab>, aliases: Record<string, string>): Partial<Tab> | null {
	if (!Object.keys(aliases).length) return null;
	const updates: Record<string, unknown> = {};
	for (const key of PROMPT_STATE_KEYS) {
		const value = tab[key];
		if (value === undefined) continue;
		const next = rewriteMergedMarkers(value, aliases);
		if (next !== value) updates[key] = next;
	}
	return Object.keys(updates).length ? (updates as Partial<Tab>) : null;
}
