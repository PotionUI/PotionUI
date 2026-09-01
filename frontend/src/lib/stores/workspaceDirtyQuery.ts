import { writable } from 'svelte/store';

// "New workspace" (TabBar) needs to know, at click time, whether the active
// tab currently has unsaved session changes — the authoritative answer only
// exists as SessionCluster's local `hasUnsavedChanges`, since that component
// only mounts for the active tab ({#key currentTab.id} in
// routes/generate/+page.svelte). This is a one-shot query/answer mailbox
// (same idiom as stores/confirm.ts and workspaceSaveRequest.ts) asked once
// per click — deliberately NOT a continuous push of the dirty flag onto the
// shared tabsStore: an earlier version of this feature did that and it
// churned the tabs array on every keystroke's dirty-flag transition, which
// intermittently stole DOM focus from the segment editor mid-type (see the
// regression this caused in session-tab-switch-preserves-draft.spec.ts).

export interface WorkspaceDirtyQuery {
	id: number;
	tabId: string;
}

interface PendingEntry {
	query: WorkspaceDirtyQuery;
	resolve: (dirty: boolean | null) => void;
	settled: boolean;
}

const current = writable<WorkspaceDirtyQuery | null>(null);
let nextId = 1;
let pending: PendingEntry | null = null;

export const activeWorkspaceDirtyQuery = { subscribe: current.subscribe };

/** Asks whichever mounted component owns `tabId`'s live session-save UI
 *  whether it's currently dirty. Resolves `null` (unknown) if nothing answers
 *  within the grace period. */
export function queryTabDirty(tabId: string, timeoutMs = 800): Promise<boolean | null> {
	return new Promise((resolve) => {
		const query: WorkspaceDirtyQuery = { id: nextId++, tabId };
		const entry: PendingEntry = { query, resolve, settled: false };
		pending = entry;
		current.set(query);

		setTimeout(() => {
			if (!entry.settled && pending === entry) {
				answerWorkspaceDirtyQuery(query.id, null);
			}
		}, timeoutMs);
	});
}

export function answerWorkspaceDirtyQuery(id: number, dirty: boolean | null) {
	if (!pending || pending.query.id !== id || pending.settled) return;
	pending.settled = true;
	pending.resolve(dirty);
	pending = null;
	current.set(null);
}
