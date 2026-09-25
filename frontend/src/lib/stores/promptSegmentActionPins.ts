import { writable } from 'svelte/store';
import { api } from '$lib/services/api/index';
import { logger } from '$lib/utils/logger';

const SETTING_KEY = 'prompt_segment_pinned_actions';

interface PromptSegmentActionPinsState {
	ids: string[];
	loaded: boolean;
}

const initialState: PromptSegmentActionPinsState = { ids: [], loaded: false };

function isStringArray(value: unknown): value is string[] {
	return Array.isArray(value) && value.every((entry) => typeof entry === 'string');
}

function createPromptSegmentActionPinsStore() {
	const { subscribe, set, update } = writable<PromptSegmentActionPinsState>(initialState);
	let loadStarted = false;

	return {
		subscribe,

		async init() {
			if (loadStarted) return;
			loadStarted = true;
			try {
				const response = await api.getClient().get('/api/settings');
				const value = response.data?.data?.[SETTING_KEY];
				if (isStringArray(value)) {
					update((state) => ({ ...state, ids: value }));
				}
			} catch (error) {
				logger.error('Failed to load pinned prompt segment actions:', error);
			} finally {
				update((state) => ({ ...state, loaded: true }));
			}
		},

		async togglePin(id: string) {
			let previous: string[] = [];
			let next: string[] = [];
			update((state) => {
				previous = state.ids;
				next = previous.includes(id) ? previous.filter((existing) => existing !== id) : [...previous, id];
				return { ...state, ids: next };
			});
			try {
				await api.getClient().put(`/api/settings/${SETTING_KEY}`, { value: next });
			} catch (error) {
				logger.error('Failed to save pinned prompt segment actions:', error);
				update((state) => ({ ...state, ids: previous }));
			}
		},

		reset() {
			loadStarted = false;
			set(initialState);
		}
	};
}

export const promptSegmentActionPins = createPromptSegmentActionPinsStore();
