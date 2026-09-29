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
	restricted: boolean;
}

const initialState: NsfwFilterState = { mode: 'blur', loaded: false, restricted: false };

function isNsfwFilterMode(value: unknown): value is NsfwFilterMode {
	return value === 'blur' || value === 'show' || value === 'hide';
}

function createNsfwFilterStore() {
	const { subscribe, set, update } = writable<NsfwFilterState>(initialState);
	let loadStarted = false;
	let preferred: NsfwFilterMode = 'blur';
	let restricted = false;

	const effective = (): NsfwFilterMode => (restricted ? 'hide' : preferred);

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
					preferred = value;
					update((state) => ({ ...state, mode: effective() }));
				}
			} catch (error) {
				logger.error('Failed to load NSFW filter preference:', error);
			} finally {
				update((state) => ({ ...state, loaded: true }));
			}
		},

		setRestricted(value: boolean) {
			if (restricted === value) return;
			restricted = value;
			update((state) => ({ ...state, mode: effective(), restricted }));
		},

		async setMode(mode: NsfwFilterMode) {
			if (restricted) return;
			const previous = preferred;
			preferred = mode;
			update((state) => ({ ...state, mode: effective() }));
			try {
				await api.getClient().put(`/api/settings/${SETTING_KEY}`, { value: mode });
			} catch (error) {
				logger.error('Failed to save NSFW filter preference:', error);
				preferred = previous;
				update((state) => ({ ...state, mode: effective() }));
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
			preferred = 'blur';
			restricted = false;
			set(initialState);
		}
	};
}

export const nsfwFilterStore = createNsfwFilterStore();

/** Files that count toward a card/modal's carousel: final, image or video. */
export function selectableMediaFiles<
	T extends { is_final?: boolean; file_type?: string; content_state?: string }
>(files: T[]): T[] {
	return files.filter(
		(file) =>
			file.content_state === 'unrated' ||
			(file.is_final !== false && ['image', 'video'].includes((file.file_type ?? '').toLowerCase()))
	);
}

export interface ContentFlagged {
	nsfw?: boolean;
	content_flagged?: boolean;
	content_state?: string;
}

export function isNsfwFile(file: ContentFlagged): boolean {
	return !!file.nsfw || !!file.content_flagged;
}

export function isUnratedFile(file: ContentFlagged): boolean {
	return file.content_state === 'unrated';
}

export function isForcedBlur(file: ContentFlagged): boolean {
	return !!file.content_flagged;
}

export function isHiddenByMode(file: ContentFlagged, mode: NsfwFilterMode): boolean {
	return mode === 'hide' && isNsfwFile(file);
}

export function shouldBlurFile(
	file: ContentFlagged,
	mode: NsfwFilterMode,
	revealed: boolean
): boolean {
	if (!isNsfwFile(file) || mode === 'hide' || revealed) return false;
	return mode === 'blur' || isForcedBlur(file);
}

/** In `hide` mode, drop nsfw files outright; other modes pass everything through. */
export function visibleMediaFiles<T extends ContentFlagged>(files: T[], mode: NsfwFilterMode): T[] {
	if (mode !== 'hide') return files;
	return files.filter((file) => !isNsfwFile(file));
}

/** In `hide` mode, a generation whose only media is nsfw is skipped entirely. */
export function isGenerationHiddenByNsfw<T extends ContentFlagged>(
	files: T[],
	mode: NsfwFilterMode
): boolean {
	if (mode !== 'hide') return false;
	return files.length > 0 && files.every((file) => isNsfwFile(file));
}
