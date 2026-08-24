import { writable, derived } from 'svelte/store';

interface ChatPanelState {
	isOpen: boolean;
}

const initialState: ChatPanelState = {
	isOpen: false
};

function createChatPanelStore() {
	const { subscribe, set, update } = writable<ChatPanelState>(initialState);

	return {
		subscribe,

		open() {
			update((state) => ({ ...state, isOpen: true }));
		},

		close() {
			update((state) => ({ ...state, isOpen: false }));
		},

		toggle() {
			update((state) => ({ ...state, isOpen: !state.isOpen }));
		},

		reset() {
			set(initialState);
		}
	};
}

export const chatPanelStore = createChatPanelStore();

export const isChatPanelOpen = derived(chatPanelStore, ($store) => $store.isOpen);
