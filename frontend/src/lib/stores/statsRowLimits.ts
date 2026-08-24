import { writable } from 'svelte/store';
import { browser } from '$app/environment';

/**
 * Per-section row-limit preference for the admin Stats page. A per-viewer
 * DISPLAY preference, not application data, so it lives in localStorage rather
 * than the settings table.
 */
export type StatsSection =
	| 'presets'
	| 'models'
	| 'samplers'
	| 'schedulers'
	| 'resolutions'
	| 'steps'
	| 'cfgs'
	| 'denoises'
	| 'storage'
	| 'presetTiming'
	| 'presetResources';

const STORAGE_KEY = 'admin-stats-row-limits';

export const STATS_ROW_LIMIT_OPTIONS = [5, 8, 10, 15, 20, 30, 50, 100] as const;

const DEFAULTS: Record<StatsSection, number> = {
	presets: 8,
	models: 8,
	samplers: 8,
	schedulers: 8,
	resolutions: 8,
	steps: 8,
	cfgs: 8,
	denoises: 8,
	storage: 30,
	presetTiming: 10,
	presetResources: 10
};

function load(): Record<StatsSection, number> {
	if (!browser) return { ...DEFAULTS };
	try {
		const stored = localStorage.getItem(STORAGE_KEY);
		if (!stored) return { ...DEFAULTS };
		const parsed = JSON.parse(stored) as Partial<Record<StatsSection, number>>;
		return { ...DEFAULTS, ...parsed };
	} catch {
		// Corrupted/unavailable localStorage must never break the page.
		return { ...DEFAULTS };
	}
}

function createStatsRowLimitsStore() {
	const { subscribe, update } = writable<Record<StatsSection, number>>(load());

	return {
		subscribe,
		setLimit(section: StatsSection, limit: number) {
			update((state) => {
				const next = { ...state, [section]: limit };
				if (browser) {
					try {
						localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
					} catch {
						// localStorage may be full/unavailable -- the in-memory value still applies.
					}
				}
				return next;
			});
		}
	};
}

export const statsRowLimits = createStatsRowLimitsStore();
