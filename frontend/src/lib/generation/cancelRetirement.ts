// Retirement AND tab-state completion for a MANUAL, page-initiated cancel
// (the Cancel button and "clear queue" action in
// routes/generate/+page.svelte), as distinct from the live
// `generation_cancelled` WebSocket event `messages/error.ts` handles.
//
// Both page commands are async: `cancelGeneration`/`clearGenerationQueue`
// call the backend, then write into "the tab" once it answers. Between the
// call and the answer, the user can switch tabs (the reactive `activeTabId`/
// `currentTab` the page used to read AFTER the await would then name a
// DIFFERENT tab), close the original tab entirely, or the original tab can
// submit/adopt a newer generation while the old one's cancellation is still
// in flight. The page's job is to capture the target tab id and the
// cancelled generation id(s) BEFORE its `await`, then hand them to the pure
// functions here, which re-read that EXACT tab's latest state (never a
// fallback to `activeTabId`/`currentTab`) before writing anything:
// `applyConfirmedCancellations` is a no-op if the tab no longer exists, and
// only clears the tab's active-generation display if that tab's
// `activeGenerationId` still IS one of the confirmed ids -- a newer
// generation the tab has since submitted or adopted is left completely
// untouched, including its queue, progress and media. This mirrors today's
// existing behavior for the "next queued run gets adopted" case: a cancel
// only ever CLEARS `activeGenerationId` here (never adopts a successor
// itself), leaving the tab orphaned so `ownership.ts`'s `resolveOwnership`
// adopts the next queued run cold off its own next live event, exactly as
// it already does for every other termination path.
//
// `retireConfirmedCancellations` (below) is unaffected by any of this --
// resource retirement is scoped to the GENERATION id via
// `tabClaimedGenerationIds` across every tab, not to one particular tab, so
// a closed or switched-away-from original tab still gets its cancelled
// generation's cache/subscription cleaned up correctly. What the page used
// to skip entirely was everything `messages/error.ts` does for the SAME
// terminal outcome: resolving a covered Director run, and retiring the
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
 * Completes a MANUAL cancel/queue-clear command against the ORIGINAL
 * target tab's latest state -- called with a `targetTabId` the caller
 * captured BEFORE its `await api.cancelGeneration(...)`/
 * `api.clearGenerationQueue(...)`, never the page's live `activeTabId`.
 *
 * - The tab no longer existing (closed while the request was in flight) is
 *   a pure no-op here: no write, no fallback to another tab. The generation
 *   resource itself still gets cleaned up -- that is
 *   `retireConfirmedCancellations`'s job, called separately, scoped to the
 *   id rather than to this tab.
 * - `confirmedIds` is filtered from `generation.queue` unconditionally (a
 *   confirmed-cancelled id has nothing left to wait for, active display or
 *   not).
 * - The active-generation display (`activeGenerationId`, `isGenerating`,
 *   `currentGeneration`, `currentProgress`) is cleared ONLY if the tab's
 *   CURRENT `activeGenerationId` is still one of `confirmedIds` -- if the
 *   tab has since submitted or adopted a different generation, that
 *   display, its progress and its media are left completely untouched.
 */
export function applyConfirmedCancellations(
	tabsStore: CancelRetirementTabsStore,
	targetTabId: string,
	confirmedIds: Iterable<string>
): void {
	const ids = confirmedIds instanceof Set ? confirmedIds : new Set(confirmedIds);
	if (ids.size === 0) return;

	const tab = get(tabsStore).tabs.find((t) => t.id === targetTabId);
	if (!tab) return;

	const activeCancelled = tab.activeGenerationId !== null && ids.has(tab.activeGenerationId);
	tabsStore.updateTab(targetTabId, {
		...(activeCancelled ? { activeGenerationId: null } : {}),
		generation: {
			...tab.generation,
			queue: (tab.generation.queue || []).filter((q) => !ids.has(q.generation_id)),
			...(activeCancelled
				? { isGenerating: false, currentGeneration: null, currentProgress: null }
				: {})
		}
	});
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
