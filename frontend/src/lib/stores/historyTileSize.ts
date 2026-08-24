import { writable } from 'svelte/store';
import { browser } from '$app/environment';

// User-selectable density for the history light-table: scales the justified
// row height. Persisted globally (a viewing preference, not session data).
export type HistoryTileSize = 'small' | 'medium' | 'large';

const STORAGE_KEY = 'history-tile-size';

export const TILE_SIZE_MULTIPLIER: Record<HistoryTileSize, number> = {
	small: 0.72,
	medium: 1,
	large: 1.35
};

function load(): HistoryTileSize {
	if (!browser) return 'medium';
	const stored = localStorage.getItem(STORAGE_KEY);
	return stored === 'small' || stored === 'large' ? stored : 'medium';
}

function createHistoryTileSizeStore() {
	const { subscribe, set } = writable<HistoryTileSize>(load());

	return {
		subscribe,
		set(size: HistoryTileSize) {
			if (browser) localStorage.setItem(STORAGE_KEY, size);
			set(size);
		}
	};
}

export const historyTileSize = createHistoryTileSizeStore();
