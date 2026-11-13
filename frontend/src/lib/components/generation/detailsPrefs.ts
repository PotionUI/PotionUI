import { writable } from 'svelte/store';

export const DETAILS_PREFS_KEY = 'potionui.details.sections';
const VERSION = 1;

export const detailsPrefs = writable<Record<string, boolean>>({});

let loaded = false;

function readStored(): Record<string, boolean> {
	try {
		const raw = localStorage.getItem(DETAILS_PREFS_KEY);
		if (!raw) return {};
		const parsed = JSON.parse(raw);
		if (!parsed || parsed.v !== VERSION || typeof parsed.sections !== 'object' || !parsed.sections) return {};
		const known: Record<string, boolean> = {};
		for (const [key, value] of Object.entries(parsed.sections)) {
			if (typeof value === 'boolean') known[key] = value;
		}
		return known;
	} catch {
		return {};
	}
}

export function loadDetailsPrefs(): void {
	if (loaded) return;
	loaded = true;
	detailsPrefs.set(readStored());
}

export function setDetailsPref(id: string, open: boolean): void {
	loaded = true;
	detailsPrefs.update((current) => {
		const next = { ...current, [id]: open };
		try {
			localStorage.setItem(DETAILS_PREFS_KEY, JSON.stringify({ v: VERSION, sections: next }));
		} catch {
			return next;
		}
		return next;
	});
}

export function forgetDetailsPrefs(): void {
	loaded = false;
	detailsPrefs.set({});
}
