/**
 * In-memory chat composer drafts, keyed by session id.
 *
 * GlobalChatPanel unmounts UnifiedAIChat on close, which destroys the
 * composer's local `userInput`/`userResources` state. This store lives at
 * module scope (outliving that mount/unmount cycle, same trick as
 * chatSession.ts) so a draft typed before closing the drawer is still there
 * on reopen. It is deliberately not persisted to localStorage — surviving a
 * page reload isn't required, only a drawer close/reopen.
 */
import { writable, get } from 'svelte/store';
import type { ResourceChipData } from '$lib/types/chat';

export interface ChatComposerDraft {
	text: string;
	resources: Record<string, ResourceChipData>;
}

function isEmptyDraft(draft: ChatComposerDraft): boolean {
	return draft.text === '' && Object.keys(draft.resources).length === 0;
}

// A conversation typed into before it has a backend session id (the session
// is created lazily on first send) shares this key - only one such
// not-yet-created conversation exists in the composer at a time.
const UNSAVED_SESSION_KEY = '__unsaved__';

function keyFor(sessionId: string | null): string {
	return sessionId ?? UNSAVED_SESSION_KEY;
}

function createChatComposerDraftsStore() {
	const { subscribe, update, set } = writable<Record<string, ChatComposerDraft>>({});

	return {
		subscribe,

		/** Save (or, if empty, drop) the draft for `sessionId`. */
		save(sessionId: string | null, draft: ChatComposerDraft) {
			const key = keyFor(sessionId);
			update((drafts) => {
				if (isEmptyDraft(draft)) {
					if (!(key in drafts)) return drafts;
					const { [key]: _removed, ...rest } = drafts;
					return rest;
				}
				return { ...drafts, [key]: draft };
			});
		},

		/** Read the draft for `sessionId`, or null if there isn't one. */
		load(sessionId: string | null): ChatComposerDraft | null {
			return get({ subscribe })[keyFor(sessionId)] ?? null;
		},

		clear(sessionId: string | null) {
			const key = keyFor(sessionId);
			update((drafts) => {
				if (!(key in drafts)) return drafts;
				const { [key]: _removed, ...rest } = drafts;
				return rest;
			});
		},

		/** Called by applyIdentityGuard on a user switch - see auth.ts. */
		reset() {
			set({});
		}
	};
}

export const chatComposerDrafts = createChatComposerDraftsStore();
