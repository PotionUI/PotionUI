import { writable } from 'svelte/store';
import { browser } from '$app/environment';

export type LibraryCardDensity = 'comfortable' | 'compact';

const STORAGE_KEY = 'admin-library-card-density-v2';

function load(): LibraryCardDensity {
	if (!browser) return 'comfortable';
	try {
		return localStorage.getItem(STORAGE_KEY) === 'compact' ? 'compact' : 'comfortable';
	} catch {
		return 'comfortable';
	}
}

function createLibraryCardDensityStore() {
	const { subscribe, set } = writable<LibraryCardDensity>(load());

	return {
		subscribe,
		set(value: LibraryCardDensity) {
			if (browser) {
				try {
					localStorage.setItem(STORAGE_KEY, value);
				} catch {}
			}
			set(value);
		}
	};
}

export const libraryCardDensity = createLibraryCardDensityStore();
