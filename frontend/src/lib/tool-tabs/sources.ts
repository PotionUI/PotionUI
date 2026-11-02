import type { MediaRef, Tab, ToolTab } from '$lib/types/tabs';
import { isToolTab } from '$lib/types/tabs';

export function mediaRefKey(ref: MediaRef | null | undefined): string | null {
	if (!ref) return null;
	return ref.relative_path || ref.path || ref.url || null;
}

export function sameMedia(a: MediaRef | null | undefined, b: MediaRef | null | undefined): boolean {
	const key = mediaRefKey(a);
	return key !== null && key === mediaRefKey(b);
}

export function mediaFileStem(ref: MediaRef | null | undefined): string | null {
	if (!ref) return null;
	const raw = ref.name || ref.relative_path || ref.path || ref.url || '';
	const base = raw.split(/[\\/]/).pop()?.split('?')[0] ?? '';
	const stem = base.includes('.') ? base.slice(0, base.lastIndexOf('.')) : base;
	return stem || null;
}

export function findToolTabFor(tabs: Tab[], source: MediaRef | null | undefined, tool?: string): ToolTab | null {
	if (!mediaRefKey(source)) return null;
	for (const tab of tabs) {
		if (!isToolTab(tab)) continue;
		if (tool && tab.tool !== tool) continue;
		if (sameMedia(tab.source, source)) return tab;
	}
	return null;
}

export type ToolTabOpenPlan = { action: 'focus'; tabId: string } | { action: 'fill'; tabId: string } | { action: 'new' };

export function planToolTabOpen(
	tabs: Tab[],
	activeTabId: string,
	tool: string,
	source: MediaRef | null
): ToolTabOpenPlan {
	const existing = findToolTabFor(tabs, source, tool);
	if (existing) return { action: 'focus', tabId: existing.id };
	if (!source) return { action: 'new' };
	const empty = tabs.filter(
		(tab): tab is ToolTab => isToolTab(tab) && tab.tool === tool && !tab.source && !(tab.dirty ?? 0) && tab.state == null
	);
	if (empty.length === 0) return { action: 'new' };
	const preferred = empty.find((tab) => tab.id === activeTabId) ?? empty[0];
	return { action: 'fill', tabId: preferred.id };
}
