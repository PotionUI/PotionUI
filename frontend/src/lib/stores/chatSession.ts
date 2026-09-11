/**
 * Conversation state for the unified AI chat panel.
 *
 * Lives in a store (not component state) because GlobalChatPanel unmounts
 * UnifiedAIChat when the panel closes — the store keeps the conversation and
 * in-flight generation flags alive across open/close cycles, and lets
 * sibling chat components (header, history rail, input) share it without
 * prop drilling.
 */
import { writable, derived } from 'svelte/store';
import type { UnifiedChatMessageData } from '$lib/types/chat';
import {
	applyToken,
	applyToolStart,
	applyToolEnd,
	applyDone,
	applyError,
	applyStatus,
	applyReplaySnapshot,
	nextClientKey
} from '$lib/utils/chatStream';

export const DEFAULT_CHAT_MODE = 'generation';

export interface ChatConversationState {
	sessionId: string | null;
	/** Mode id; immutable once the conversation has messages. */
	mode: string;
	messages: UnifiedChatMessageData[];
	/** Session-scoped subtractive tool filter (names the user unticked). */
	disabledTools: string[];
	isGenerating: boolean;
	error: string;
	/** Identity of the turn currently streaming (or last streamed), handed
	 * out by `beginTurn()`. A read-only mirror of the store's own private
	 * counter — see `beginTurn` — used by a streaming handler's recovery path
	 * (`isRecoveryStillCurrent` in chatStream.ts) to detect that a newer turn
	 * has since taken over before publishing a delayed result. */
	turnSeq: number;
	/** How many messages this session has, as last reported by the backend
	 * (`ChatSessionResponse.message_count`) — the true total, independent of
	 * how many of them `messages` currently holds. A fresh, never-loaded
	 * conversation keeps this in step with `messages.length` via `addMessage`
	 * rather than leaving it at 0. */
	messageCount: number;
	/** Whether `messages` is a tail window (`GET .../{id}?tail=60`) with
	 * older messages not yet loaded — drives the "Load earlier messages" row
	 * and `loadEarlier()`'s guard. */
	hasEarlier: boolean;
	/** A `loadEarlier()` fetch is in flight. */
	loadingEarlier: boolean;
}

function initialState(mode: string = DEFAULT_CHAT_MODE): ChatConversationState {
	return {
		sessionId: null,
		mode,
		messages: [],
		disabledTools: [],
		isGenerating: false,
		error: '',
		turnSeq: 0,
		messageCount: 0,
		hasEarlier: false,
		loadingEarlier: false
	};
}

export type ChatStreamEvent = { type: string; data: any };

function createChatSessionStore() {
	const { subscribe, set, update } = writable<ChatConversationState>(initialState());
	// The allocation source of truth for turnSeq — deliberately NOT reset by
	// `newConversation`/`reset` (which replace the whole state via
	// initialState()), so a stale handler from before a reset can never
	// collide with a fresh turn's id by both landing on the same small
	// number. `state.turnSeq` is kept in sync as a readable mirror.
	let turnSeq = 0;

	return {
		subscribe,

		/** Merge a partial state change. */
		patch(partial: Partial<ChatConversationState>) {
			update((s) => ({ ...s, ...partial }));
		},

		/**
		 * Allocate a new turn identity and publish it to the store, for a
		 * streaming handler (live send or reattach) to capture at creation
		 * time and compare against later — see `isRecoveryStillCurrent`.
		 * Call once per turn, right alongside adding its streaming
		 * placeholder message.
		 */
		beginTurn(): number {
			turnSeq += 1;
			const seq = turnSeq;
			update((s) => ({ ...s, turnSeq: seq }));
			return seq;
		},

		/** Replace the message list via a pure transform. */
		updateMessages(fn: (messages: UnifiedChatMessageData[]) => UnifiedChatMessageData[]) {
			update((s) => ({ ...s, messages: fn(s.messages) }));
		},

		/** A message with neither a persisted `id` nor a `clientKey` already
		 * (the common case — an optimistic send, a fresh streaming placeholder)
		 * gets one assigned here, so a keyed `{#each}` in the UI always has a
		 * stable key to render it with, from its very first frame. */
		addMessage(message: UnifiedChatMessageData) {
			const withKey = message.id || message.clientKey ? message : { ...message, clientKey: nextClientKey() };
			update((s) => ({ ...s, messages: [...s.messages, withKey], messageCount: s.messageCount + 1 }));
		},

		/** Start a fresh conversation in the given mode (clears session + messages). */
		newConversation(mode: string = DEFAULT_CHAT_MODE) {
			set(initialState(mode));
		},

		/** Adopt a session loaded from the backend (keeps its persisted mode).
		 * `messages` may be a tail window rather than the whole conversation —
		 * `meta.messageCount`/`meta.hasEarlier` carry the backend's own
		 * accounting of that (see `ChatSessionWithMessagesResponse`); omitted,
		 * they default to "this IS the whole conversation" so callers that
		 * still load everything (no `tail`) don't need to pass them. */
		loadedSession(
			session: { id: string; mode?: string },
			messages: UnifiedChatMessageData[],
			meta: { messageCount?: number; hasEarlier?: boolean } = {}
		) {
			update((s) => ({
				...s,
				sessionId: session.id,
				mode: session.mode || s.mode,
				messages,
				disabledTools: [],
				isGenerating: false,
				error: '',
				messageCount: meta.messageCount ?? messages.length,
				hasEarlier: meta.hasEarlier ?? false,
				loadingEarlier: false
			}));
		},

		/** Prepend an older page of messages fetched by `loadEarlier()` (the
		 * `before`-cursor page immediately preceding what's currently loaded),
		 * and record whether there's still more before THAT. */
		prependMessages(olderMessages: UnifiedChatMessageData[], hasEarlier: boolean) {
			update((s) => ({
				...s,
				messages: [...olderMessages, ...s.messages],
				hasEarlier,
				loadingEarlier: false
			}));
		},

		/**
		 * Apply a streaming SSE event to the message list. `accumulated` is the
		 * caller-maintained full streamed text (required for `token` events).
		 */
		applyStreamEvent(event: ChatStreamEvent, opts: { accumulated?: string } = {}) {
			update((s) => {
				switch (event.type) {
					case 'token':
						return { ...s, messages: applyToken(s.messages, opts.accumulated ?? '') };
					case 'tool_start':
						return { ...s, messages: applyToolStart(s.messages, event.data || {}) };
					case 'tool_end':
						return { ...s, messages: applyToolEnd(s.messages, event.data || {}) };
					case 'status':
						return { ...s, messages: applyStatus(s.messages, event.data || {}) };
					case 'replay_snapshot':
						return { ...s, messages: applyReplaySnapshot(s.messages, event.data || {}) };
					case 'done':
						return { ...s, messages: applyDone(s.messages, event.data || {}) };
					case 'error':
						return { ...s, messages: applyError(s.messages) };
					default:
						return s;
				}
			});
		},

		reset() {
			set(initialState());
		}
	};
}

export const chatSession = createChatSessionStore();

/** Mode is fixed once the conversation has any messages — `messageCount` (the
 * backend's own total) covers a loaded session whose tail window happens to
 * be empty for some other reason `messages` alone wouldn't. */
export const modeLocked = derived(chatSession, ($s) => $s.messages.length > 0 || $s.messageCount > 0);
