import { describe, expect, it } from 'vitest';
import { indexingDoneSummary, indexingIsRunning, indexingIsVisible, indexingPercent } from './indexingDisplay';
import type { IndexingStatus } from '$lib/services/api/models';

function status(overrides: Partial<IndexingStatus> = {}): IndexingStatus {
	return { state: 'idle', ...overrides };
}

describe('indexingIsRunning', () => {
	it('is false without a status', () => {
		expect(indexingIsRunning(null)).toBe(false);
	});

	it('is true while scanning or indexing', () => {
		expect(indexingIsRunning(status({ state: 'scanning' }))).toBe(true);
		expect(indexingIsRunning(status({ state: 'indexing' }))).toBe(true);
	});

	it('is true when a restart is pending, even if the state itself looks terminal', () => {
		expect(indexingIsRunning(status({ state: 'done', restart_pending: true }))).toBe(true);
	});

	it('is false once idle/done/failed/cancelled/blocked with no restart pending', () => {
		for (const state of ['idle', 'done', 'failed', 'cancelled', 'blocked'] as const) {
			expect(indexingIsRunning(status({ state }))).toBe(false);
		}
	});
});

describe('indexingIsVisible', () => {
	it('is false without a status or while idle', () => {
		expect(indexingIsVisible(null)).toBe(false);
		expect(indexingIsVisible(status({ state: 'idle' }))).toBe(false);
	});

	it('stays visible while idle when files were skipped as duplicates', () => {
		expect(indexingIsVisible(status({ state: 'idle', skipped_duplicates_total: 2 }))).toBe(true);
		expect(indexingIsVisible(status({ state: 'idle', skipped_duplicates_total: 0 }))).toBe(false);
	});

	it('is true for every non-idle state', () => {
		for (const state of ['scanning', 'indexing', 'done', 'failed', 'cancelled', 'blocked'] as const) {
			expect(indexingIsVisible(status({ state }))).toBe(true);
		}
	});
});

describe('indexingPercent', () => {
	it('is null without a status, outside "indexing", or before the total is known', () => {
		expect(indexingPercent(null)).toBeNull();
		expect(indexingPercent(status({ state: 'scanning' }))).toBeNull();
		expect(indexingPercent(status({ state: 'indexing', processed: 0, total: 0 }))).toBeNull();
	});

	it('rounds processed/total to a 0-100 percent', () => {
		expect(indexingPercent(status({ state: 'indexing', processed: 47, total: 128 }))).toBe(37);
		expect(indexingPercent(status({ state: 'indexing', processed: 128, total: 128 }))).toBe(100);
	});
});

describe('indexingDoneSummary', () => {
	it('is null without a status or outside "done"', () => {
		expect(indexingDoneSummary(null)).toBeNull();
		expect(indexingDoneSummary(status({ state: 'indexing' }))).toBeNull();
	});

	it('is "zero" when nothing was found on disk', () => {
		expect(indexingDoneSummary(status({ state: 'done', found_on_disk: 0 }))).toEqual({ kind: 'zero' });
	});

	it('is "up_to_date" when everything found was already indexed', () => {
		expect(indexingDoneSummary(status({ state: 'done', found_on_disk: 908, indexed: 0 }))).toEqual({
			kind: 'up_to_date',
			found: 908
		});
	});

	it('is "new_indexed" and computes the already-indexed remainder when new files were indexed', () => {
		expect(indexingDoneSummary(status({ state: 'done', found_on_disk: 128, indexed: 20 }))).toEqual({
			kind: 'new_indexed',
			indexed: 20,
			alreadyIndexed: 108,
			found: 128
		});
	});
});
