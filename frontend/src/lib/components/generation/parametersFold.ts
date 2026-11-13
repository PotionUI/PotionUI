import { writable } from 'svelte/store';

export const PARAMETERS_FOLD_KEY = 'potionui.params.expanded';

export const parametersExpanded = writable(false);

let loaded = false;

function readStored(): boolean {
	try {
		return localStorage.getItem(PARAMETERS_FOLD_KEY) === '1';
	} catch {
		return false;
	}
}

export function loadFoldPreference(): void {
	if (loaded) return;
	loaded = true;
	parametersExpanded.set(readStored());
}

export function setFoldPreference(expanded: boolean): void {
	loaded = true;
	parametersExpanded.set(expanded);
	try {
		localStorage.setItem(PARAMETERS_FOLD_KEY, expanded ? '1' : '0');
	} catch {
		return;
	}
}

export function forgetFoldPreference(): void {
	loaded = false;
	parametersExpanded.set(false);
}
