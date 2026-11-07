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
}

export const CHAT_READY_TIMEOUT_MS = 15000;

type ComposerRequest = { kind: 'prefill'; text: string; replace: boolean };

function createChatPanelStore() {
	const { subscribe, set, update } = writable<ChatPanelState>(initialState);
	let composer: ChatComposer | null = null;
	let pending: ComposerRequest[] = [];
	let readyTimer: ReturnType<typeof setTimeout> | null = null;

	function dropPending() {
		if (readyTimer) clearTimeout(readyTimer);
		readyTimer = null;
		pending = [];
	}

	function run(target: ChatComposer, request: ComposerRequest) {
		target.prefill(request.text, request.replace);
	}

	function enqueue(request: ComposerRequest) {
		update((state) => ({ ...state, isOpen: true }));
		if (composer) {
			run(composer, request);
			return;
		}
		pending.push(request);
		readyTimer ??= setTimeout(dropPending, CHAT_READY_TIMEOUT_MS);
	}

	return {
		subscribe,

		open() {
			update((state) => ({ ...state, isOpen: true }));
		},

		close() {
			update((state) => ({ ...state, isOpen: false }));
			dropPending();
		},

		toggle() {
			let closed = false;
			update((state) => {
				closed = state.isOpen;
				return { ...state, isOpen: !state.isOpen };
			});
			if (closed) dropPending();
		},

		reset() {
			set(initialState);
			dropPending();
		},

		prefill(text: string, options: ChatPrefillOptions = {}) {
			if (typeof text !== 'string') return;
			enqueue({ kind: 'prefill', text, replace: options.replace === true });
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
