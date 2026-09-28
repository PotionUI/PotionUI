import { describe, expect, it } from 'vitest';
import { indexingIsRunning, indexingIsVisible, indexingPercent } from './indexingDisplay';
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
