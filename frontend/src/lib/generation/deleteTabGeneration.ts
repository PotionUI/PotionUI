import { get } from 'svelte/store';
import { findGenerationTab, generationTabs, type GenerationTab } from '$lib/types/tabs';
import type { APIResponse } from '$lib/types/api';
import {
	applyConfirmedCancellations,
	retireConfirmedCancellations,
	type CancelRetirementTabsStore
} from './cancelRetirement';
import { tabClaimedGenerationIds } from './messages/ownership';
import { retireGeneration } from './messages/generationOutputs';

const LIVE_STATUSES = new Set(['pending', 'queued', 'running']);

type TabView = Pick<GenerationTab, 'activeGenerationId' | 'generation'>;

export interface TabDeleteTarget {
	tabId: string;
	generationId: string;
}

export interface DeleteTabGenerationDeps {
	tabsStore: CancelRetirementTabsStore;
	unsubscribe: (generationId: string) => void;
	cancel: (generationId: string) => Promise<APIResponse<{ cancel_notice: string | null }>>;
	remove: (generationId: string) => Promise<APIResponse<unknown>>;
}

function shownGenerationId(tab: TabView): string | null {
	const current = tab.generation.currentGeneration;
	const currentId = current?.id ?? current?.generation_id ?? null;
	return (typeof currentId === 'string' && currentId) || tab.activeGenerationId || null;
}

export function tabDeleteTarget(tab: (TabView & { id: string }) | null | undefined): TabDeleteTarget | null {
	if (!tab?.generation) return null;
	const generationId = shownGenerationId(tab);
	return generationId ? { tabId: tab.id, generationId } : null;
}

export function isGenerationLive(tab: TabView, generationId: string): boolean {
	if (tab.activeGenerationId === generationId) return true;
	if ((tab.generation.queue || []).some((queued) => queued.generation_id === generationId)) return true;
	const current = tab.generation.currentGeneration;
	const currentId = current?.id ?? current?.generation_id;
	return currentId === generationId && LIVE_STATUSES.has(String(current?.status ?? ''));
}

export function clearDeletedGeneration(
	tabsStore: CancelRetirementTabsStore,
	tabId: string,
	generationId: string,
	shownBefore = false
): void {
	const tab = findGenerationTab(get(tabsStore).tabs, tabId);
	if (!tab) return;
	const queue = (tab.generation.queue || []).filter((queued) => queued.generation_id !== generationId);
	const blankSinceCancel = shownBefore && !tab.activeGenerationId && !tab.generation.currentGeneration;
	const shows = shownGenerationId(tab) === generationId || blankSinceCancel;
	if (!shows) {
		tabsStore.updateTab(tabId, { generation: { ...tab.generation, queue } });
		return;
	}
	tabsStore.updateTab(tabId, {
		...(tab.activeGenerationId === generationId ? { activeGenerationId: null } : {}),
		generation: {
			...tab.generation,
			queue,
			isGenerating: false,
			currentGeneration: null,
			currentProgress: null,
			batchImages: [],
			batchVideos: [],
			batchAudios: [],
			batchMeshes: [],
			artifacts: [],
			pluginOutputs: {},
			workbenchIndex: 0,
			workbenchTotal: 0,
			cancelNotice: null,
			cancelledGenerationId: null
		}
	});
}

function retireIfUnclaimed(generationId: string, deps: DeleteTabGenerationDeps): void {
	const claimed = generationTabs(get(deps.tabsStore).tabs).some((tab) => tabClaimedGenerationIds(tab).has(generationId));
	if (!claimed) retireGeneration(generationId, deps.unsubscribe);
}

function failure(response: APIResponse<unknown>, fallback: string): Error {
	return new Error(response.message || response.error || fallback);
}

export async function deleteTabGeneration(target: TabDeleteTarget, deps: DeleteTabGenerationDeps): Promise<void> {
	const { tabId, generationId } = target;
	const tab = findGenerationTab(get(deps.tabsStore).tabs, tabId);
	const live = !!tab && isGenerationLive(tab, generationId);
	const shownBefore = !!tab && shownGenerationId(tab) === generationId;

	if (live) {
		const cancelled = await deps.cancel(generationId);
		if (!cancelled.success) throw failure(cancelled, 'Could not stop this generation, so it was not deleted.');
		applyConfirmedCancellations(deps.tabsStore, tabId, [generationId], cancelled.data?.cancel_notice ?? null);
		retireConfirmedCancellations([generationId], { tabsStore: deps.tabsStore, unsubscribe: deps.unsubscribe });
	}

	const removed = await deps.remove(generationId);
	if (!removed.success) throw failure(removed, 'Failed to delete generation');

	clearDeletedGeneration(deps.tabsStore, tabId, generationId, shownBefore);
	if (!live) retireIfUnclaimed(generationId, deps);
}
