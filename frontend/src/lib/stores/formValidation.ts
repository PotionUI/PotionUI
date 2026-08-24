import { writable } from 'svelte/store';

/**
 * Per generation-tab field-validation errors from the last 422
 * `form_validation_failed` response to `POST /api/generations/start`.
 * Keyed by tab id -> field name -> messages, so switching between generation
 * tabs naturally shows each tab's own outstanding errors (see
 * `src/routes/generate/+page.svelte`'s `startGeneration()`).
 */
function createFormValidationStore() {
	const { subscribe, update, set } = writable<Record<string, Record<string, string[]>>>({});

	return {
		subscribe,
		setErrors(tabId: string, fieldErrors: Record<string, string[]>) {
			update((all) => ({ ...all, [tabId]: fieldErrors }));
		},
		clearAll(tabId: string) {
			update((all) => {
				if (!(tabId in all)) return all;
				const next = { ...all };
				delete next[tabId];
				return next;
			});
		},
		clearField(tabId: string, fieldName: string) {
			update((all) => {
				const current = all[tabId];
				if (!current || !(fieldName in current)) return all;
				const nextFieldErrors = { ...current };
				delete nextFieldErrors[fieldName];
				return { ...all, [tabId]: nextFieldErrors };
			});
		},
		reset() {
			set({});
		}
	};
}

export const formValidationStore = createFormValidationStore();
