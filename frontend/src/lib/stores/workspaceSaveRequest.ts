import { writable } from 'svelte/store';

// "New workspace" (TabBar) needs to trigger the REAL save flow before wiping
// tabs, but that flow (quick-save / save-as) lives inside SessionCluster,
// which only mounts for the active tab ({#key currentTab.id} in
// routes/generate/+page.svelte). Rather than duplicating SessionCluster's
// save logic in TabBar, this is a promise-based request/response mailbox —
// same idiom as stores/confirm.ts — that TabBar posts to and SessionCluster
// answers.

export interface WorkspaceSaveRequest {
	id: number;
	tabId: string;
}

interface PendingEntry {
	request: WorkspaceSaveRequest;
	resolve: (ok: boolean) => void;
	settled: boolean;
}

const current = writable<WorkspaceSaveRequest | null>(null);
let nextId = 1;
let pending: PendingEntry | null = null;

export const activeWorkspaceSaveRequest = { subscribe: current.subscribe };

/** Asks whichever mounted component owns `tabId`'s live session-save UI to
 *  run its real save action, and waits for the outcome. If nothing answers
 *  within the grace period (no SessionCluster mounted for this tab), resolves
 *  `true` — nothing was found to save, so the caller should proceed as if
 *  there was nothing to save rather than hang. */
export function requestTabSave(tabId: string, timeoutMs = 5000): Promise<boolean> {
	return new Promise((resolve) => {
		const request: WorkspaceSaveRequest = { id: nextId++, tabId };
		const entry: PendingEntry = { request, resolve, settled: false };
		pending = entry;
		current.set(request);

		setTimeout(() => {
			if (!entry.settled && pending === entry) {
				settleWorkspaceSaveRequest(request.id, true);
			}
		}, timeoutMs);
	});
}

export function settleWorkspaceSaveRequest(id: number, ok: boolean) {
	if (!pending || pending.request.id !== id || pending.settled) return;
	pending.settled = true;
	pending.resolve(ok);
	pending = null;
	current.set(null);
}
