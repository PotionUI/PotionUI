import { writable } from 'svelte/store';
import { api } from '$lib/services/api/index';
import { logger } from '$lib/utils/logger';

/**
 * `workbench_single_result_gallery` - a SYSTEM setting, admin-controlled, but
 * every user's workbench needs its value to decide whether the gallery strip
 * renders for a single-output run. `GET /api/settings` serves it to non-admin
 * users too (see PUBLIC_SYSTEM_SETTING_KEYS in src/features/settings/routes.py).
 */

const SETTING_KEY = 'workbench_single_result_gallery';

interface WorkbenchGallerySettingsState {
	singleResultGallery: boolean;
	loaded: boolean;
}

const initialState: WorkbenchGallerySettingsState = { singleResultGallery: false, loaded: false };

function createWorkbenchGallerySettingsStore() {
	const { subscribe, update, set } = writable<WorkbenchGallerySettingsState>(initialState);
	let loadStarted = false;

	return {
		subscribe,

		/** Fetch the setting once; safe to call from every consumer. */
		async init() {
			if (loadStarted) return;
			loadStarted = true;
			try {
				const response = await api.getClient().get('/api/settings');
				const value = response.data?.data?.[SETTING_KEY];
				if (typeof value === 'boolean') {
					update((state) => ({ ...state, singleResultGallery: value }));
				}
			} catch (error) {
				logger.error('Failed to load workbench gallery setting:', error);
			} finally {
				update((state) => ({ ...state, loaded: true }));
			}
		},

		/** Drops the loaded value and the one-shot `init()` guard, so a
		 * different user's next `init()` re-fetches instead of reusing this
		 * one's value. */
		reset() {
			loadStarted = false;
			set(initialState);
		}
	};
}

export const workbenchGallerySettingsStore = createWorkbenchGallerySettingsStore();
