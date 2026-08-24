import { writable } from 'svelte/store';

/**
 * Tracks the single segment last written by an LLM-proposed
 * `<tool_action type="update_segment">` apply, so the segmented prompt
 * editor can flash a quiet highlight on it and scroll it into view.
 *
 * In-memory only: no localStorage, no server round-trip. Gone on reload,
 * reset per window. `nonce` bumps on every `set` (even re-applying the same
 * segment id) so re-triggering the scroll doesn't depend on identity change.
 */
export interface LastAppliedSegment {
	segmentId: string;
	nonce: number;
}

function createLastAppliedSegmentStore() {
	const { subscribe, update } = writable<LastAppliedSegment | null>(null);
	let nonce = 0;

	return {
		subscribe,

		set(segmentId: string) {
			nonce += 1;
			update(() => ({ segmentId, nonce }));
		},

		/** Clear, optionally only if the current entry matches `segmentId`. */
		clear(segmentId?: string) {
			update((current) => {
				if (!current) return current;
				if (segmentId !== undefined && current.segmentId !== segmentId) return current;
				return null;
			});
		}
	};
}

export const lastAppliedSegment = createLastAppliedSegmentStore();
