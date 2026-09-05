// Retirement for a MANUAL, page-initiated cancel (the Cancel button and
// "clear queue" action in routes/generate/+page.svelte), as distinct from
// the live `generation_cancelled` WebSocket event `messages/error.ts`
// handles. The page already updates its own tab's `activeGenerationId` and
// `generation.queue` directly for a cancel request the backend confirms --
// that stays in the page (this module is not a replacement for it). What
// the page used to skip was everything `messages/error.ts` does for the
// SAME terminal outcome: resolving a covered Director run, and retiring the
// generation's cached outputs/WebSocket subscription/retired-marker through
// `generationOutputs.ts`'s `retireGeneration` (dropping straight to
// `ws.unsubscribe` bypassed both the subscription bookkeeping in
// `restore/subscriptions.ts` and PERF-12's cache/retired-set cleanup, so a
// belated `generation_cancelled` for an already-manually-cancelled id found
// no owning tab and never cleaned either up).
import { get } from 'svelte/store';
import type { Readable } from 'svelte/store';
import type { Tab } from '$lib/types/tabs';
import {
	directorShotIdsFor,
	withDirectorRunTerminal,
	withoutDirectorRunLink
} from './messages/directorRuns';
import { tabClaimedGenerationIds } from './messages/ownership';
import { retireGeneration } from './messages/generationOutputs';

/** Minimal surface `retireConfirmedCancellations` needs from `tabsStore` --
 *  the real store (`$lib/stores/tabs`) satisfies this as-is. */
export interface CancelRetirementTabsStore extends Readable<{ tabs: Tab[]; activeTabId: string }> {
	updateTab(tabId: string, updates: Partial<Tab>): void;
}

export interface CancelRetirementDeps {
	tabsStore: CancelRetirementTabsStore;
	/** The page's unified `unsubscribeGeneration` -- releases subscription
	 *  bookkeeping (`restore/subscriptions.ts`) AND calls the socket's own
	 *  unsubscribe, same contract `reconcileTabGenerations`'s `unsubscribe`
	 *  option and `dispatchGenerationMessage`'s `DispatchDeps.unsubscribe`
	 *  use. */
	unsubscribe: (generationId: string) => void;
	now?: () => number;
}

/**
 * Retires every SERVER-CONFIRMED cancelled generation id -- callers must
 * pass only ids the backend actually reported cancelled (a rejected/failed
 * cancel request must call this with nothing, leaving every live resource:
 * listener, cache, bookkeeping, tab state, untouched).
 *
 * For each id: resolves any Director run it covers to 'failed' through the
 * SAME identity-guarded reducer (`withDirectorRunTerminal`) the live
 * `generation_cancelled` handler uses -- Director runs have no separate
 * "cancelled" state, a cancellation resolves exactly like a failure there
 * too. Then, ONLY IF no tab still claims the id (`tabClaimedGenerationIds`,
 * the exact orphaned-resource check `tabsStore.removeTab` already uses),
 * drops its cache/subscription/retired-marker through the shared
 * `retireGeneration`. A generation is submitted by exactly one tab and a
 * cancel is always issued by that same tab, so "still claimed by a
 * DIFFERENT tab" is not expected in practice -- the check exists for the
 * same reason `removeTab`'s does: a shared Director run predecessor
 * reference or a future multi-tab feature must never lose a resource
 * another consumer still needs.
 *
 * Call this AFTER the page applies its own tab-state update (clearing
 * `activeGenerationId`/filtering `generation.queue` for the cancelling
 * tab), so "still claimed" here only ever reflects a genuinely different
 * tab, never a stale pre-update snapshot of the very tab that just
 * cancelled.
 */
export function retireConfirmedCancellations(
	generationIds: Iterable<string>,
	deps: CancelRetirementDeps
): void {
	const now = deps.now ?? Date.now;

	for (const generationId of generationIds) {
		const tabs = get(deps.tabsStore).tabs;
		const directorTab = tabs.find((tab) => directorShotIdsFor(tab, generationId) !== null);
		if (directorTab) {
			const shotIds = directorShotIdsFor(directorTab, generationId)!;
			deps.tabsStore.updateTab(directorTab.id, {
				directorRuns: withDirectorRunTerminal(directorTab, shotIds, 'failed', null, now(), generationId),
				directorRunLinks: withoutDirectorRunLink(directorTab, generationId)
			});
		}

		// Re-read: the Director-link removal above (when it happened) can
		// itself be what makes this id fully unclaimed.
		const latestTabs = get(deps.tabsStore).tabs;
		const stillClaimed = latestTabs.some((tab) => tabClaimedGenerationIds(tab).has(generationId));
		if (!stillClaimed) {
			retireGeneration(generationId, deps.unsubscribe);
		}
	}
}
