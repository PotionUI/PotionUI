import { writable } from 'svelte/store';
import { api } from '$lib/services/api/index';
import { logger } from '$lib/utils/logger';

/**
 * Per-user NSFW content mode (`media_nsfw_filter_mode`, a USER-type setting,
 * so it follows the account across devices). Which items count as NSFW is
 * decided server-side (`file.nsfw` on history payloads); this store only
 * answers "what does this user want done with them?" - blur in place, show
 * unfiltered, or hide entirely. Reveal state is deliberately NOT here: it
 * lives in `nsfwReveal.ts`, shared across every component instance so a file
 * revealed in one place stays revealed everywhere for the rest of the session.
 */

const SETTING_KEY = 'media_nsfw_filter_mode';

export type NsfwFilterMode = 'blur' | 'show' | 'hide';

interface NsfwFilterState {
	mode: NsfwFilterMode;
	loaded: boolean;
}

const initialState: NsfwFilterState = { mode: 'blur', loaded: false };

function isNsfwFilterMode(value: unknown): value is NsfwFilterMode {
	return value === 'blur' || value === 'show' || value === 'hide';
}

function createNsfwFilterStore() {
	const { subscribe, set, update } = writable<NsfwFilterState>(initialState);
	let loadStarted = false;

	return {
		subscribe,

		/** Fetch the preference once; safe to call from every consumer. */
		async init() {
			if (loadStarted) return;
			loadStarted = true;
			try {
				const response = await api.getClient().get('/api/settings');
				const value = response.data?.data?.[SETTING_KEY];
				if (isNsfwFilterMode(value)) {
					update((state) => ({ ...state, mode: value }));
				}
			} catch (error) {
				logger.error('Failed to load NSFW filter preference:', error);
			} finally {
				update((state) => ({ ...state, loaded: true }));
			}
		},

		async setMode(mode: NsfwFilterMode) {
			let previous: NsfwFilterMode = 'blur';
			update((state) => {
				previous = state.mode;
				return { ...state, mode };
			});
			try {
				await api.getClient().put(`/api/settings/${SETTING_KEY}`, { value: mode });
			} catch (error) {
				logger.error('Failed to save NSFW filter preference:', error);
				update((state) => ({ ...state, mode: previous }));
			}
		},

		/**
		 * Drops the loaded preference and the one-shot `init()` guard, so the
		 * next consumer to call `init()` (after a different user signs in)
		 * re-fetches instead of silently reusing the previous user's mode.
		 * Only clears state — never calls `setMode()`, so nothing is written
		 * back to the (now different) account until its real preference loads.
		 */
		reset() {
			loadStarted = false;
			set(initialState);
		}
	};
}

export const nsfwFilterStore = createNsfwFilterStore();

/** Files that count toward a card/modal's carousel: final, image or video. */
export function selectableMediaFiles<T extends { is_final: boolean; file_type: string }>(
	files: T[]
): T[] {
	return files.filter(
		(file) => file.is_final !== false && ['image', 'video'].includes(file.file_type.toLowerCase())
	);
}

/** In `hide` mode, drop nsfw files outright; other modes pass everything through. */
export function visibleMediaFiles<T extends { nsfw?: boolean }>(
	files: T[],
	mode: NsfwFilterMode
): T[] {
	if (mode !== 'hide') return files;
	return files.filter((file) => !file.nsfw);
}

/** In `hide` mode, a generation whose only media is nsfw is skipped entirely. */
export function isGenerationHiddenByNsfw<T extends { nsfw?: boolean }>(
	files: T[],
	mode: NsfwFilterMode
): boolean {
	if (mode !== 'hide') return false;
	return files.length > 0 && files.every((file) => !!file.nsfw);
}
