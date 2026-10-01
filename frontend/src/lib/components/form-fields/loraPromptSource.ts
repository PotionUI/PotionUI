import type { Tab } from '$lib/types/tabs';
import { combineSegmentsToString } from '$lib/utils/generationOrchestrator';

type PromptTab = Pick<Tab, 'id' | 'prompt' | 'promptSegments'>;

export function promptTextForTab(tabs: PromptTab[], tabId: string | undefined): string {
	if (!tabId) return '';
	const tab = tabs.find((candidate) => candidate.id === tabId);
	if (!tab) return '';
	const segments = tab.promptSegments || [];
	return segments.length > 0 ? combineSegmentsToString(segments) : tab.prompt || '';
}
