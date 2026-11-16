import { get } from 'svelte/store';
import { api } from '$lib/services/api/index';
import { tabsStore, type OpenToolTabOptions } from '$lib/stores/tabs';
import type { MediaRef } from '$lib/types/tabs';
import { findEditTab, prepareContinue, type ContinueDeps, type TabLike } from './image/recentEdits';
import { withSavedCopy } from './image/state';

export type ContinueOutcome =
	| { kind: 'focused'; tabId: string }
	| { kind: 'opened'; tabId: string }
	| { kind: 'missing' }
	| { kind: 'source-missing' }
	| { kind: 'failed' };

export interface ContinueTabs {
	list: () => TabLike[];
	focus: (tabId: string) => void;
	open: (tool: string, source: MediaRef, options: OpenToolTabOptions) => string;
}

export interface ContinueEnv extends Pick<ContinueDeps, 'getEdit' | 'library'> {
	tabs: ContinueTabs;
}

export type SavedCopyWriter = (state: Record<string, unknown>, itemId: string) => Record<string, unknown>;

const savedCopyWriters = new Map<string, SavedCopyWriter>([['image', withSavedCopy]]);

export function registerSavedCopyWriter(tool: string, writer: SavedCopyWriter): void {
	savedCopyWriters.set(tool, writer);
}

export function defaultContinueEnv(): ContinueEnv {
	return {
		getEdit: (id) => api.getEdit(id),
		library: api,
		tabs: {
			list: () => get(tabsStore).tabs,
			focus: (tabId) => tabsStore.setActiveTab(tabId),
			open: (tool, source, options) => tabsStore.openToolTab(tool, source, options)
		}
	};
}

export async function continueEditInNewTab(
	editId: string,
	pictureItemId: string | null,
	env: ContinueEnv = defaultContinueEnv()
): Promise<ContinueOutcome> {
	const prepared = await prepareContinue(editId, {
		getEdit: env.getEdit,
		library: env.library,
		findOpen: (id) => findEditTab(env.tabs.list(), id)
	});
	if (prepared.kind === 'focused') {
		env.tabs.focus(prepared.tabId);
		return prepared;
	}
	if (prepared.kind !== 'ready') return prepared;
	const writer = savedCopyWriters.get(prepared.record.tool);
	const state = pictureItemId && writer ? writer(prepared.state, pictureItemId) : prepared.state;
	const tabId = env.tabs.open(prepared.record.tool, prepared.source, {
		activeTool: prepared.activeTool,
		state,
		fresh: true
	});
	env.tabs.focus(tabId);
	return { kind: 'opened', tabId };
}
