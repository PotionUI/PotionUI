import { writable } from 'svelte/store';

/**
 * Tracks, per segment id, which chat `<tool_action type="update_segment">`
 * card was the one last applied to that segment — so ChatMessage can mark
 * that specific variant card as the applied one (a quiet ring + an
 * "Applied" label on its Apply button) instead of leaving every proposed
 * variant looking equally unapplied.
 *
 * Keyed by the segment id resolved at apply time (id-first, index-fallback —
 * the same resolution `locateSegmentIndex` performs), so a later apply of a
 * different variant of the same segment overwrites the entry and the marker
 * moves automatically. Card identity is (messageId, actionIndex): a card
 * never needs to re-derive the resolved segment id itself, it just checks
 * whether any entry in the map points back at it.
 *
 * In-memory only: no localStorage, no server round-trip. Gone on reload.
 * `nonce` bumps on every `set`, including re-applying the same
 * (segmentId, messageId, actionIndex) triple.
 */
export interface AppliedSegmentAction {
	messageId: string;
	actionIndex: number;
	nonce: number;
}

function createAppliedSegmentActionsStore() {
	const { subscribe, update } = writable<Record<string, AppliedSegmentAction>>({});
	let nonce = 0;

	return {
		subscribe,

		set(segmentId: string, messageId: string, actionIndex: number) {
			nonce += 1;
			update((current) => ({ ...current, [segmentId]: { messageId, actionIndex, nonce } }));
		},

		/** Clear one segment's entry, or every entry when `segmentId` is omitted. */
		clear(segmentId?: string) {
			update((current) => {
				if (segmentId === undefined) return {};
				if (!(segmentId in current)) return current;
				const next = { ...current };
				delete next[segmentId];
				return next;
			});
		}
	};
}

export const appliedSegmentActions = createAppliedSegmentActionsStore();

/** True when the card at (messageId, actionIndex) is the currently applied variant of its segment. */
export function isAppliedSegmentAction(
	map: Record<string, AppliedSegmentAction>,
	messageId: string,
	actionIndex: number
): boolean {
	if (!messageId) return false;
	for (const entry of Object.values(map)) {
		if (entry.messageId === messageId && entry.actionIndex === actionIndex) return true;
	}
	return false;
}
