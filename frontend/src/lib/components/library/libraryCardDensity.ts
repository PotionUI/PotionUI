import { writable } from 'svelte/store';
import { browser } from '$app/environment';

export type LibraryCardDensity = 'compact' | 'dense';

const STORAGE_KEY = 'admin-library-card-density';

function load(): LibraryCardDensity {
	if (!browser) return 'compact';
	try {
		return localStorage.getItem(STORAGE_KEY) === 'dense' ? 'dense' : 'compact';
	} catch {
		return 'compact';
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
