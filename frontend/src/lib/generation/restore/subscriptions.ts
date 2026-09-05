// One shared per-generation subscription lifecycle for a live listener
// owner (in production, the page's own `WebSocketService` instance),
// consulted by BOTH fresh submission (routes/generate/+page.svelte's
// startGeneration and its Video Director shot submission) and reconnect
// reconciliation (reconcile.ts). Neither call site may decide on its own
// whether to register a socket listener: `WebSocketService` keeps a
// `Set<MessageHandler>` per generation id and restores it on reconnect, so
// two independent call sites each handing it a fresh closure for the SAME
// id -- submit, then the first reconciliation pass finding that same id
// still pending/running -- would leave TWO listeners and deliver every
// message twice. `ensureSubscribed` is the single gate both go through: the
// first caller for a given (owner, generationId) pair actually requests the
// subscription; every later caller for the SAME pair is a no-op, so the
// underlying socket never sees a second `subscribe` call for it.
//
// Bookkeeping here is deliberately NOT tied to the tab/routing state
// (`generation.queue`) -- that survives an SPA navigate away and back,
// while the live socket does not (a fresh mount builds a brand new
// `WebSocketService`). Keying on `owner` instead means a replacement socket
// starts with no memory of its own and subscribes every routed live
// generation exactly once, matching its own fresh, empty listener map.
const requestedIdsByOwner = new Map<unknown, Set<string>>();

function recordFor(owner: unknown): Set<string> {
	let ids = requestedIdsByOwner.get(owner);
	if (!ids) {
		ids = new Set();
		requestedIdsByOwner.set(owner, ids);
	}
	return ids;
}

/**
 * Requests a subscription for `generationId` against `owner` -- calls
 * `subscribe()` (the caller's actual `ws.subscribe(...)`) only the FIRST
 * time this exact (owner, generationId) pair is ever seen; every subsequent
 * call for the same pair, from either call site, is a silent no-op.
 * `owner === undefined` opts out of dedup entirely (always subscribes) --
 * only relevant to a caller that genuinely has no stable owner to key on.
 */
export function ensureSubscribed(owner: unknown, generationId: string, subscribe: () => void): void {
	if (owner === undefined) {
		subscribe();
		return;
	}
	const ids = recordFor(owner);
	if (ids.has(generationId)) return;
	ids.add(generationId);
	subscribe();
}

/** True once `ensureSubscribed` has actually requested a subscription for
 *  `generationId` against `owner`. */
export function isSubscriptionRequested(owner: unknown, generationId: string): boolean {
	return requestedIdsByOwner.get(owner)?.has(generationId) ?? false;
}

/** Releases one generation id's bookkeeping for `owner` -- called from the
 *  page's single `unsubscribeGeneration` wrapper (used by every terminal/
 *  retirement path: `dispatchGenerationMessage`'s own unsubscribe, the
 *  `setGenerationUnsubscribeHandler` seam, and `reconcileTabGenerations`'s
 *  `unsubscribe` option), so a page session that submits/restores many
 *  generations over its lifetime doesn't hold every one of them in memory
 *  until teardown -- only whatever is still actually in flight. */
export function releaseSubscription(owner: unknown, generationId: string): void {
	requestedIdsByOwner.get(owner)?.delete(generationId);
}

/** Drops an owner's ENTIRE subscription bookkeeping -- a backstop called
 *  from the page's `onDestroy` once the owner (its `WebSocketService`
 *  instance) itself is retired, for any id `releaseSubscription` never
 *  reached (e.g. a still-in-flight generation abandoned mid-session). */
export function clearSubscriptionOwner(owner: unknown): void {
	requestedIdsByOwner.delete(owner);
}
