import { browser } from '$app/environment';

export const storage = {
	get(key: string, fallback?: string): string | null {
		if (!browser) return fallback ?? null;
		return localStorage.getItem(key) ?? fallback ?? null;
	},
	set(key: string, value: string): void {
		if (!browser) return;
		localStorage.setItem(key, value);
	},
	remove(key: string): void {
		if (!browser) return;
		localStorage.removeItem(key);
	},
	getJSON<T>(key: string, fallback?: T): T | null {
		if (!browser) return fallback ?? null;
		try {
			const raw = localStorage.getItem(key);
			return raw ? (JSON.parse(raw) as T) : (fallback ?? null);
		} catch {
			return fallback ?? null;
		}
	},
	setJSON(key: string, value: unknown): void {
		if (!browser) return;
		localStorage.setItem(key, JSON.stringify(value));
	},
};
