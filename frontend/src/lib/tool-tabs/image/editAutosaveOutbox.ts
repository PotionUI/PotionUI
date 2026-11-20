import type { EditSource, EditStateDoc } from '$lib/services/api/edits';
import { scopedStorage } from '$lib/stores/identityScopedStorage';
import { EDIT_AUTOSAVE_OUTBOX_KEY } from '$lib/stores/editAutosaveKeys';
import type { OutboxStorage } from '$lib/stores/tabDraftOutbox';

export interface PendingEdit {
	source: EditSource | null;
	state: EditStateDoc;
}

interface OutboxState {
	pending: Record<string, PendingEdit>;
}

function isPending(value: unknown): value is PendingEdit {
	if (!value || typeof value !== 'object') return false;
	const entry = value as Partial<PendingEdit>;
	return !!entry.state && typeof entry.state === 'object' && !Array.isArray(entry.state);
}

function parseState(raw: string | null): OutboxState {
	if (!raw) return { pending: {} };
	try {
		const parsed = JSON.parse(raw) as { pending?: Record<string, unknown> };
		const pending: Record<string, PendingEdit> = {};
		for (const [tabId, entry] of Object.entries(parsed.pending ?? {})) {
			if (isPending(entry)) pending[tabId] = entry;
		}
		return { pending };
	} catch {
		return { pending: {} };
	}
}

export function createEditAutosaveOutbox(storage: OutboxStorage) {
	let cache: OutboxState | null = null;

	function state(): OutboxState {
		if (!cache) {
			try {
				cache = parseState(storage.get(EDIT_AUTOSAVE_OUTBOX_KEY));
			} catch {
				cache = { pending: {} };
			}
		}
		return cache;
	}

	function persist(): boolean {
		try {
			storage.set(EDIT_AUTOSAVE_OUTBOX_KEY, JSON.stringify(state()));
			return true;
		} catch {
			return false;
		}
	}

	return {
		reset(): void {
			cache = null;
		},

		put(tabId: string, entry: PendingEdit): boolean {
			state().pending[tabId] = entry;
			return persist();
		},

		pending(tabId: string): PendingEdit | null {
			return state().pending[tabId] ?? null;
		},

		pendingTabIds(): string[] {
			return Object.keys(state().pending);
		},

		ack(tabId: string, sent: PendingEdit): void {
			const current = state().pending[tabId];
			if (!current || current.state !== sent.state) return;
			delete state().pending[tabId];
			persist();
		},

		drop(tabId: string): void {
			if (!(tabId in state().pending)) return;
			delete state().pending[tabId];
			persist();
		},

		prune(liveTabIds: readonly string[]): void {
			const live = new Set(liveTabIds);
			let changed = false;
			for (const id of Object.keys(state().pending)) {
				if (live.has(id)) continue;
				delete state().pending[id];
				changed = true;
			}
			if (changed) persist();
		}
	};
}

export type EditAutosaveOutbox = ReturnType<typeof createEditAutosaveOutbox>;

export const editAutosaveOutbox = createEditAutosaveOutbox(scopedStorage);
