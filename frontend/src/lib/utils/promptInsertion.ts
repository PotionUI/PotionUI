import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { combineSegmentsToString } from '$lib/utils/generationOrchestrator';
import { randomUUID } from '$lib/utils/uuid';
import type { Segment } from '$lib/types/segments';

export type InsertTriggerResult = 'inserted' | 'duplicate' | 'unavailable';

/**
 * Insert a LoRA trigger word into the active tab's positive prompt.
 *
 * Returns:
 * - 'unavailable' when there is no active tab, the tab uses multi-prompt
 *   tabs (`promptTabs.length > 1`), or the tab is in prompt-relay mode
 *   (`tab.promptRelay` present - relay tabs don't have a single flat prompt
 *   to append to).
 * - 'duplicate' when the trigger (case-insensitive) is already present in
 *   the combined prompt text.
 * - 'inserted' when the trigger was appended to the last content segment
 *   (or a new segment was created if there were none).
 */
export function insertTriggerIntoActivePrompt(trigger: string): InsertTriggerResult {
	const state = get(tabsStore);
	const tab = state.tabs.find((t) => t.id === state.activeTabId);

	if (!tab) return 'unavailable';
	if (tab.promptTabs && tab.promptTabs.length > 1) return 'unavailable';
	if (tab.promptRelay) return 'unavailable';

	const segments: Segment[] = tab.promptSegments || [];
	const currentText = segments.length > 0 ? combineSegmentsToString(segments) : tab.prompt || '';

	if (currentText.toLowerCase().includes(trigger.toLowerCase())) {
		return 'duplicate';
	}

	let updatedSegments: Segment[];

	// Find the last content-type segment (skip breaks) to append to.
	let lastContentIndex = -1;
	for (let i = segments.length - 1; i >= 0; i--) {
		if (segments[i].type !== 'break') {
			lastContentIndex = i;
			break;
		}
	}

	if (lastContentIndex >= 0) {
		updatedSegments = segments.map((seg, i) => {
			if (i !== lastContentIndex) return seg;
			const trimmed = seg.content?.trim() || '';
			return {
				...seg,
				content: trimmed.length > 0 ? `${trimmed}, ${trigger}` : trigger
			};
		});
	} else {
		updatedSegments = [
			...segments,
			{
				id: randomUUID(),
				content: trigger,
				type: 'content'
			}
		];
	}

	tabsStore.updateTab(tab.id, {
		promptSegments: updatedSegments,
		prompt: combineSegmentsToString(updatedSegments)
	});

	return 'inserted';
}
