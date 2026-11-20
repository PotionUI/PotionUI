import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { generationTabs } from '$lib/types/tabs';
import {
	ELSEWHERE_BUSY_REFRESH_MS,
	ELSEWHERE_IDLE_REFRESH_MS,
	ELSEWHERE_MIN_GAP_MS,
	ingestQueueSnapshot,
	refreshRunningElsewhere,
	resetElsewhereForTests,
	runningElsewhere,
	startElsewhereWatch
} from './store';

const SNAPSHOT = {
	running: [{ generation_id: 'g1', tab_id: null, has_tab: false, status: 'running', preset_name: 'SDXL', origin: { kind: 'mcp', key_id: 'k', key_name: 'n' } }],
	pending: []
} as never;

function fakeApi(response: unknown = { success: true, data: SNAPSHOT }) {
	return { getGenerationQueue: vi.fn(async () => response as never) };
}

beforeEach(() => {
	tabsStore.reset();
	resetElsewhereForTests();
});

afterEach(() => {
	vi.useRealTimers();
});

describe('runningElsewhere', () => {
	it('follows both the queue snapshot and the open tabs', () => {
		expect(get(runningElsewhere).total).toBe(0);
		ingestQueueSnapshot(SNAPSHOT);
		expect(get(runningElsewhere).total).toBe(1);
		const tabId = generationTabs(get(tabsStore).tabs)[0].id;
		tabsStore.updateTab(tabId, { activeGenerationId: 'g1' });
		expect(get(runningElsewhere).total).toBe(0);
	});
});

describe('refreshRunningElsewhere', () => {
	it('shares one request between callers that arrive together', async () => {
		const api = fakeApi();
		await Promise.all([refreshRunningElsewhere(api), refreshRunningElsewhere(api)]);
		expect(api.getGenerationQueue).toHaveBeenCalledTimes(1);
		expect(get(runningElsewhere).total).toBe(1);
	});

	it('does not ask again right after a snapshot arrived from somewhere else', async () => {
		const api = fakeApi();
		ingestQueueSnapshot(SNAPSHOT, 10_000);
		await refreshRunningElsewhere(api, { now: () => 10_000 + ELSEWHERE_MIN_GAP_MS - 1 });
		expect(api.getGenerationQueue).not.toHaveBeenCalled();
		await refreshRunningElsewhere(api, { now: () => 10_000 + ELSEWHERE_MIN_GAP_MS + 1 });
		expect(api.getGenerationQueue).toHaveBeenCalledTimes(1);
	});

	it('keeps the last list when a request fails', async () => {
		ingestQueueSnapshot(SNAPSHOT, 0);
		const api = { getGenerationQueue: vi.fn(async () => Promise.reject(new Error('offline'))) };
		await refreshRunningElsewhere(api, { force: true });
		expect(get(runningElsewhere).total).toBe(1);
	});
});

describe('startElsewhereWatch', () => {
	it('asks once soon after start, then slowly while idle and faster while something runs', async () => {
		vi.useFakeTimers();
		const api = fakeApi();
		let busy = false;
		const stop = startElsewhereWatch({ api, hasItems: () => busy, isVisible: () => true, firstDelayMs: 100 });
		await vi.advanceTimersByTimeAsync(100);
		expect(api.getGenerationQueue).toHaveBeenCalledTimes(1);
		await vi.advanceTimersByTimeAsync(ELSEWHERE_IDLE_REFRESH_MS);
		expect(api.getGenerationQueue).toHaveBeenCalledTimes(2);
		busy = true;
		await vi.advanceTimersByTimeAsync(ELSEWHERE_IDLE_REFRESH_MS);
		expect(api.getGenerationQueue).toHaveBeenCalledTimes(3);
		await vi.advanceTimersByTimeAsync(ELSEWHERE_BUSY_REFRESH_MS);
		expect(api.getGenerationQueue).toHaveBeenCalledTimes(4);
		stop();
		await vi.advanceTimersByTimeAsync(ELSEWHERE_IDLE_REFRESH_MS * 3);
		expect(api.getGenerationQueue).toHaveBeenCalledTimes(4);
	});

	it('stays quiet while the page is hidden', async () => {
		vi.useFakeTimers();
		const api = fakeApi();
		const stop = startElsewhereWatch({ api, hasItems: () => false, isVisible: () => false, firstDelayMs: 100 });
		await vi.advanceTimersByTimeAsync(ELSEWHERE_IDLE_REFRESH_MS * 2);
		expect(api.getGenerationQueue).not.toHaveBeenCalled();
		stop();
	});
});
