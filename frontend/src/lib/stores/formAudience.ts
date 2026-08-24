import { writable, get } from 'svelte/store';
import { browser } from '$app/environment';
import type { FormAudience } from '$lib/utils/audienceFilter';

const STORAGE_KEY = 'potionui-form-audience';

function load(): FormAudience {
	if (!browser) return 'simple';
	const stored = localStorage.getItem(STORAGE_KEY);
	return stored === 'advanced' ? 'advanced' : 'simple';
}

function createFormAudienceStore() {
	const store = writable<FormAudience>(load());

	function setAudience(value: FormAudience) {
		if (browser) localStorage.setItem(STORAGE_KEY, value);
		store.set(value);
	}

	return {
		subscribe: store.subscribe,
		set: setAudience,
		toggle() {
			setAudience(get(store) === 'simple' ? 'advanced' : 'simple');
		}
	};
}

export const formAudienceStore = createFormAudienceStore();
