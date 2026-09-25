import { readable, type Readable } from 'svelte/store';
import { resourceMarkerRegex, type PromptResourceSpec } from './promptResources';

export interface UsageSegment {
	content?: string | null;
	enabled?: boolean;
	isDisabled?: boolean;
}

export interface UsagePromptTab {
	promptSegments?: UsageSegment[] | null;
	negativePromptSegments?: UsageSegment[] | null;
}

export interface UsageTab {
	promptSegments?: UsageSegment[] | null;
	negativePromptSegments?: UsageSegment[] | null;
	promptTabs?: UsagePromptTab[] | null;
}

export type ResourceUseCounts = Record<string, Record<string, number>>;

export interface PromptResourceUsage {
	specs: PromptResourceSpec[];
	counts: ResourceUseCounts;
}

export const PROMPT_RESOURCE_USAGE_CONTEXT_KEY = Symbol('prompt-resource-usage');

export const EMPTY_PROMPT_RESOURCE_USAGE: Readable<PromptResourceUsage> = readable({ specs: [], counts: {} });

function segmentActive(segment: UsageSegment): boolean {
	return segment.enabled !== false && segment.isDisabled !== true;
}

export function countResourceReferences(
	segmentGroups: readonly (readonly UsageSegment[] | null | undefined)[]
): ResourceUseCounts {
	const counts: ResourceUseCounts = {};
	for (const segments of segmentGroups) {
		if (!segments) continue;
		for (const segment of segments) {
			const text = segment?.content;
			if (!text || !segmentActive(segment) || !text.includes('@[')) continue;
			const re = resourceMarkerRegex();
			let match: RegExpExecArray | null;
			while ((match = re.exec(text)) !== null) {
				const byItem = (counts[match[1]] ??= {});
				byItem[match[2]] = (byItem[match[2]] ?? 0) + 1;
			}
		}
	}
	return counts;
}

export function resourceUseCountsEqual(a: ResourceUseCounts, b: ResourceUseCounts): boolean {
	if (a === b) return true;
	const fields = Object.keys(a);
	if (fields.length !== Object.keys(b).length) return false;
	for (const field of fields) {
		const left = a[field];
		const right = b[field];
		if (!right) return false;
		const items = Object.keys(left);
		if (items.length !== Object.keys(right).length) return false;
		for (const item of items) if (left[item] !== right[item]) return false;
	}
	return true;
}

export function tabResourceSegmentGroups(tab: UsageTab | null | undefined): (UsageSegment[] | null | undefined)[] {
	if (!tab) return [];
	if (tab.promptTabs && tab.promptTabs.length > 0) {
		return tab.promptTabs.flatMap((promptTab) => [promptTab.promptSegments, promptTab.negativePromptSegments]);
	}
	return [tab.promptSegments, tab.negativePromptSegments];
}

export function resourceUseCount(counts: ResourceUseCounts, field: string, itemKey: string | null): number {
	if (!itemKey) return 0;
	return counts[field]?.[itemKey] ?? 0;
}
