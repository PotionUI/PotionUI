import { writable, type Readable } from 'svelte/store';
import { api } from '$lib/services/api/index';
import type { EditsApi } from '$lib/services/api/edits';
import { tabsStore } from '$lib/stores/tabs';
import { toasts } from '$lib/stores/toast';
import type { MenuRecentEdit } from '$lib/editors/menuItems';
import { continueEditInNewTab, type ContinueOutcome } from '../continueEdit';
import { editLabel } from './recentEdits';

export const MENU_RECENT_COUNT = 3;

export interface MenuRecents extends Readable<MenuRecentEdit[] | null> {
	load(): Promise<void>;
}

export function createMenuRecents(edits: Pick<EditsApi, 'listEdits'> = api): MenuRecents {
	const store = writable<MenuRecentEdit[] | null>(null);
	let loading = false;
	return {
		subscribe: store.subscribe,
		async load() {
			if (loading) return;
			loading = true;
			try {
				const response = await edits.listEdits({ tool: 'image', limit: MENU_RECENT_COUNT, offset: 0 });
				const list = response.success && response.data ? response.data.edits : [];
				store.set(
					list.slice(0, MENU_RECENT_COUNT).map((edit) => {
						const meta = (edit.source.name ?? '').trim();
						return { id: edit.id, label: editLabel(edit).text, ...(meta ? { meta } : {}) };
					})
				);
			} catch {
				store.set([]);
			} finally {
				loading = false;
			}
		}
	};
}

export function explainContinue(result: ContinueOutcome): void {
	if (result.kind === 'missing') toasts.error('That edit no longer exists.');
	else if (result.kind === 'source-missing') toasts.error('The picture for this edit is no longer available.');
	else if (result.kind === 'failed') toasts.error('Could not open this edit. Try again.');
}

export async function openRecentEdit(id: string): Promise<void> {
	explainContinue(await continueEditInNewTab(id, null));
}

export function openAllEdits(): void {
	tabsStore.openToolTab('image', null, { fresh: true });
}

