import { writable } from 'svelte/store';
import type { AppliedState } from '$lib/formulas/types';

const store = writable<Record<string, AppliedState>>({});
let revision = 0;

export const formulaApplied = {
	subscribe: store.subscribe,
	set(tabId: string, state: Omit<AppliedState, 'revision'>) {
		revision += 1;
		store.update((all) => ({ ...all, [tabId]: { ...state, revision } }));
	},
	clear(tabId: string) {
		store.update((all) => {
			if (!(tabId in all)) return all;
			const { [tabId]: _removed, ...rest } = all;
			return rest;
		});
	}
};
