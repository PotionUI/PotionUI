import { goto } from '$app/navigation';
import { get } from 'svelte/store';
import { activeTab, tabsStore } from '$lib/stores/tabs';
import type { MediaRef } from '$lib/types/tabs';
import { lastToolFor, rememberTool } from './lastTool';
import { mediaRefForItem, planShortcut, type ShortcutItem } from './shortcuts';

function goToGenerate(): void {
	if (typeof window === 'undefined' || window.location.pathname.startsWith('/generate')) return;
	setTimeout(() => void goto('/generate'), 0);
}

export function openToolShortcut(actionId: string, items: readonly ShortcutItem[]): boolean {
	const plan = planShortcut(actionId, items);
	if (!plan) return false;
	const ids: string[] = [];
	for (const group of plan) {
		rememberTool(group.tool, group.activeTool);
		ids.push(
			...tabsStore.openToolTabs(group.tool, group.items.map(mediaRefForItem), {
				activeTool: group.activeTool,
				hint: group.hint ?? null
			})
		);
	}
	if (ids[0]) tabsStore.setActiveTab(ids[0]);
	goToGenerate();
	return true;
}

export function openFieldInEditor(kind: 'image' | 'video', item: unknown, field: string | null): string | null {
	const record = (item && typeof item === 'object' ? item : null) as Record<string, unknown> | null;
	const url = typeof record?.url === 'string' ? record.url : '';
	const path = typeof record?.path === 'string' ? record.path : '';
	const relative = typeof record?.relative_path === 'string' ? record.relative_path : undefined;
	if (!url && !path && !relative) return null;
	const source: MediaRef = {
		path,
		url: url || undefined,
		relative_path: relative,
		name: typeof record?.name === 'string' ? record.name : undefined,
		type: kind
	};
	const id = tabsStore.openToolTab(kind, source, {
		activeTool: lastToolFor(kind),
		hint: field ? { sendTo: { tabId: get(activeTab).id, field } } : null
	});
	tabsStore.setActiveTab(id);
	return id;
}
