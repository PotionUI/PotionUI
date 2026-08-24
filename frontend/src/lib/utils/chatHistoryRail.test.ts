import { describe, it, expect, beforeEach, vi } from 'vitest';

// vitest runs with `environment: 'node'`, so `$app/environment`'s `browser` is
// false and `storage` no-ops — force it true and stub `localStorage` (same
// pattern as soundSettings.test.ts) so the round trip is actually exercised.
vi.mock('$app/environment', () => ({ browser: true }));

import {
	HISTORY_RAIL_COLLAPSED_KEY,
	loadHistoryRailCollapsed,
	saveHistoryRailCollapsed
} from './chatHistoryRail';

describe('chatHistoryRail', () => {
	beforeEach(() => {
		const store = new Map<string, string>();
		(globalThis as any).localStorage = {
			getItem: (key: string) => store.get(key) ?? null,
			setItem: (key: string, value: string) => void store.set(key, value),
			removeItem: (key: string) => void store.delete(key),
			clear: () => store.clear()
		};
	});

	it('defaults to collapsed when nothing is persisted', () => {
		expect(loadHistoryRailCollapsed()).toBe(true);
	});

	it('round-trips an expanded state', () => {
		saveHistoryRailCollapsed(false);
		expect(loadHistoryRailCollapsed()).toBe(false);
		expect(localStorage.getItem(HISTORY_RAIL_COLLAPSED_KEY)).toBe('false');
	});

	it('round-trips a collapsed state', () => {
		saveHistoryRailCollapsed(false);
		saveHistoryRailCollapsed(true);
		expect(loadHistoryRailCollapsed()).toBe(true);
	});

	it('treats a malformed stored value as collapsed', () => {
		localStorage.setItem(HISTORY_RAIL_COLLAPSED_KEY, 'garbage');
		expect(loadHistoryRailCollapsed()).toBe(true);
	});
});
