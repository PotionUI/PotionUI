import { writable, derived } from 'svelte/store';

interface ChatPanelState {
	isOpen: boolean;
}

const initialState: ChatPanelState = {
	isOpen: false
};

export interface ChatPrefillOptions {
	replace?: boolean;
}

export interface ChatComposer {
	prefill(text: string, replace: boolean): void;
	send(text: string): Promise<void>;
}

export const CHAT_READY_TIMEOUT_MS = 15000;

type ComposerRequest =
	| { kind: 'prefill'; text: string; replace: boolean }
	| { kind: 'send'; text: string; resolve: () => void; reject: (error: Error) => void };

function createChatPanelStore() {
	const { subscribe, set, update } = writable<ChatPanelState>(initialState);
	let composer: ChatComposer | null = null;
	let pending: ComposerRequest[] = [];
	let readyTimer: ReturnType<typeof setTimeout> | null = null;

	function dropPending(reason: string) {
		if (readyTimer) clearTimeout(readyTimer);
		readyTimer = null;
		const dropped = pending;
		pending = [];
		for (const request of dropped) {
			if (request.kind === 'send') request.reject(new Error(reason));
		}
	}

	function run(target: ChatComposer, request: ComposerRequest) {
		if (request.kind === 'prefill') {
			target.prefill(request.text, request.replace);
			return;
		}
		let sent: Promise<void>;
		try {
			sent = target.send(request.text);
		} catch (error) {
			sent = Promise.reject(error);
		}
		sent.then(request.resolve, (error) =>
			request.reject(error instanceof Error ? error : new Error(String(error)))
		);
	}

	function enqueue(request: ComposerRequest) {
		update((state) => ({ ...state, isOpen: true }));
		if (composer) {
			run(composer, request);
			return;
		}
		pending.push(request);
		readyTimer ??= setTimeout(() => dropPending('The chat is not available'), CHAT_READY_TIMEOUT_MS);
	}

	return {
		subscribe,

		open() {
			update((state) => ({ ...state, isOpen: true }));
		},

		close() {
			update((state) => ({ ...state, isOpen: false }));
			dropPending('The chat panel was closed before the chat was ready');
		},

		toggle() {
			let closed = false;
			update((state) => {
				closed = state.isOpen;
				return { ...state, isOpen: !state.isOpen };
			});
			if (closed) dropPending('The chat panel was closed before the chat was ready');
		},

		reset() {
			set(initialState);
			dropPending('The chat panel was reset before the chat was ready');
		},

		prefill(text: string, options: ChatPrefillOptions = {}) {
			if (typeof text !== 'string') return;
			enqueue({ kind: 'prefill', text, replace: options.replace === true });
		},

		send(text: string): Promise<void> {
			if (typeof text !== 'string' || !text.trim()) {
				return Promise.reject(new TypeError('chat.send needs a non-empty text'));
			}
			return new Promise<void>((resolve, reject) => enqueue({ kind: 'send', text, resolve, reject }));
		},

		attachComposer(next: ChatComposer): () => void {
			composer = next;
			if (readyTimer) clearTimeout(readyTimer);
			readyTimer = null;
			const queued = pending;
			pending = [];
			for (const request of queued) run(next, request);
			return () => {
				if (composer === next) composer = null;
			};
		}
	};
}

export const chatPanelStore = createChatPanelStore();

export const isChatPanelOpen = derived(chatPanelStore, ($store) => $store.isOpen);
