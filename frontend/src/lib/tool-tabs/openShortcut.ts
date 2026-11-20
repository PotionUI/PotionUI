import { goto } from '$app/navigation';
import { get } from 'svelte/store';
import { activeTab, tabsStore } from '$lib/stores/tabs';
import { toasts } from '$lib/stores/toast';
import type { MediaRef } from '$lib/types/tabs';
import { CONTINUE_ACTION, continuableLink } from './continueTarget';
import { continueEditInNewTab } from './continueEdit';
import { lastToolFor, rememberTool } from './lastTool';
import { EDIT_ACTION, mediaRefForItem, planShortcut, type ShortcutItem } from './shortcuts';

function goToGenerate(): void {
	if (typeof window === 'undefined' || window.location.pathname.startsWith('/generate')) return;
	setTimeout(() => void goto('/generate'), 0);
}

const FALLBACK_COPY = {
	missing: 'That edit is no longer there. The picture opened as it is.',
	'source-missing': 'The picture that edit started from is no longer there. The picture opened as it is.',
	failed: 'The edit could not be opened. The picture opened as it is.'
} as const;

function openContinue(items: readonly ShortcutItem[]): boolean {
	const link = continuableLink(items);
	if (!link) return openToolShortcut(EDIT_ACTION, items);
	const picture = items[0];
	void continueEditInNewTab(link.editId, picture.itemId ?? picture.id).then((outcome) => {
		if (outcome.kind === 'opened' || outcome.kind === 'focused') {
			goToGenerate();
			return;
		}
		toasts.show('info', FALLBACK_COPY[outcome.kind], { title: 'Continue this edit' });
		openToolShortcut(EDIT_ACTION, items);
	});
	return true;
}

export function openToolShortcut(actionId: string, items: readonly ShortcutItem[]): boolean {
	if (actionId === CONTINUE_ACTION) return openContinue(items);
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
