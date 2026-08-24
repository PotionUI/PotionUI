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
	applyStatus
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
}

function initialState(mode: string = DEFAULT_CHAT_MODE): ChatConversationState {
	return {
		sessionId: null,
		mode,
		messages: [],
		disabledTools: [],
		isGenerating: false,
		error: ''
	};
}

export type ChatStreamEvent = { type: string; data: any };

function createChatSessionStore() {
	const { subscribe, set, update } = writable<ChatConversationState>(initialState());

	return {
		subscribe,

		/** Merge a partial state change. */
		patch(partial: Partial<ChatConversationState>) {
			update((s) => ({ ...s, ...partial }));
		},

		/** Replace the message list via a pure transform. */
		updateMessages(fn: (messages: UnifiedChatMessageData[]) => UnifiedChatMessageData[]) {
			update((s) => ({ ...s, messages: fn(s.messages) }));
		},

		addMessage(message: UnifiedChatMessageData) {
			update((s) => ({ ...s, messages: [...s.messages, message] }));
		},

		/** Start a fresh conversation in the given mode (clears session + messages). */
		newConversation(mode: string = DEFAULT_CHAT_MODE) {
			set(initialState(mode));
		},

		/** Adopt a session loaded from the backend (keeps its persisted mode). */
		loadedSession(
			session: { id: string; mode?: string },
			messages: UnifiedChatMessageData[]
		) {
			update((s) => ({
				...s,
				sessionId: session.id,
				mode: session.mode || s.mode,
				messages,
				disabledTools: [],
				isGenerating: false,
				error: ''
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

/** Mode is fixed once the conversation has any messages. */
export const modeLocked = derived(chatSession, ($s) => $s.messages.length > 0);
