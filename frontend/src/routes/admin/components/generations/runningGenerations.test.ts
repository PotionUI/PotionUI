import { describe, it, expect } from 'vitest';
import type { AdminGenerationQueue } from '$lib/services/admin-api';
import {
	RUNNING_PAGE_SIZE,
	cancelErrorMessage,
	clampPage,
	elapsedLabel,
	mapRunningRows,
	ownerLabel,
	pageSlice,
	progressPercent,
	settleStopping,
	totalPages
} from './runningGenerations';

const lookups = {
	usernameFor: (id: string) => ({ u1: 'alice' })[id as 'u1'],
	presetNameFor: (id: string) => ({ p1: 'Flux Dev' })[id as 'p1'],
	backendNameFor: (id: string) => ({ b1: 'Local GPU' })[id as 'b1']
};

const queue: AdminGenerationQueue = {
	running: [
		{ generation_id: 'r2', backend_id: 'b1', preset_id: 'p1', tab_id: 't', user_id: 'u1', progress: 0.5, created_at: 200, started_at: 210 },
		{ generation_id: 'r1', backend_id: 'b1', preset_id: 'p9', tab_id: null, user_id: 'setup', progress: null, created_at: 100, started_at: 100 }
	],
	pending: [
		{ generation_id: 'q2', backend_id: 'b1', preset_id: 'p1', tab_id: 'x', user_id: 'u1', queue_position: 3, created_at: 300 },
		{ generation_id: 'q1', backend_id: null, preset_id: null, tab_id: null, user_id: 'u2', queue_position: 1, created_at: 250 }
	]
};

describe('mapRunningRows', () => {
	const rows = mapRunningRows(queue, lookups);

	it('lists running first (oldest first), then queued by position', () => {
		expect(rows.map((r) => r.id)).toEqual(['r1', 'r2', 'q1', 'q2']);
		expect(rows.map((r) => r.state)).toEqual(['running', 'running', 'pending', 'pending']);
	});

	it('resolves owner, preset and backend names with fallbacks', () => {
		const byId = Object.fromEntries(rows.map((r) => [r.id, r]));
		expect(byId.r2.owner).toBe('alice');
		expect(byId.r2.preset).toBe('Flux Dev');
		expect(byId.r2.backend).toBe('Local GPU');
		expect(byId.r1.owner).toBe('Setup / recipe');
		expect(byId.r1.preset).toBe('p9');
		expect(byId.q1.owner).toBe('u2');
		expect(byId.q1.preset).toBe('Unknown preset');
		expect(byId.q1.backend).toBe('—');
	});

	it('flags runs started without a tab, except setup runs which already say so', () => {
		const byId = Object.fromEntries(rows.map((r) => [r.id, r]));
		expect(byId.r2.noTab).toBe(false);
		expect(byId.r1.noTab).toBe(false);
		expect(byId.q1.noTab).toBe(true);
	});

	it('converts progress and times', () => {
		const byId = Object.fromEntries(rows.map((r) => [r.id, r]));
		expect(byId.r2.progress).toBe(50);
		expect(byId.r1.progress).toBeNull();
		expect(byId.r2.sinceMs).toBe(210_000);
		expect(byId.q2.position).toBe(3);
		expect(byId.q2.sinceMs).toBe(300_000);
	});

	it('is empty without a snapshot', () => {
		expect(mapRunningRows(null, lookups)).toEqual([]);
	});
});

describe('progressPercent', () => {
	it('accepts fractions and percentages and clamps', () => {
		expect(progressPercent(0.25)).toBe(25);
		expect(progressPercent(42)).toBe(42);
		expect(progressPercent(150)).toBe(100);
		expect(progressPercent(-3)).toBe(0);
		expect(progressPercent(null)).toBeNull();
	});
});

describe('elapsedLabel', () => {
	it('formats seconds, minutes and hours from the start time', () => {
		expect(elapsedLabel(1_000_000, 1_042_000)).toBe('42s');
		expect(elapsedLabel(1_000_000, 1_000_000 + 185_000)).toBe('3m 5s');
		expect(elapsedLabel(0, 3_725_000)).toMatch(/^1h/);
	});

	it('never goes negative and has a dash without a start', () => {
		expect(elapsedLabel(5_000, 1_000)).toBe('0s');
		expect(elapsedLabel(null, 1_000)).toBe('—');
	});
});

describe('ownerLabel', () => {
	it('handles missing, setup and unknown owners', () => {
		expect(ownerLabel(null, lookups.usernameFor)).toBe('Unknown');
		expect(ownerLabel('setup', lookups.usernameFor)).toBe('Setup / recipe');
		expect(ownerLabel('u1', lookups.usernameFor)).toBe('alice');
		expect(ownerLabel('zzz', lookups.usernameFor)).toBe('zzz');
	});
});

describe('paging', () => {
	const items = Array.from({ length: RUNNING_PAGE_SIZE * 2 + 3 }, (_, i) => i);

	it('slices bounded pages', () => {
		expect(pageSlice(items, 1)).toHaveLength(RUNNING_PAGE_SIZE);
		expect(pageSlice(items, 3)).toHaveLength(3);
		expect(totalPages(items.length)).toBe(3);
		expect(totalPages(0)).toBe(1);
	});

	it('clamps a page that no longer exists after rows drop out', () => {
		expect(clampPage(5, items.length)).toBe(3);
		expect(clampPage(3, 2)).toBe(1);
		expect(clampPage(0, 10)).toBe(1);
	});
});

describe('cancelErrorMessage', () => {
	it('reads the server message from detail, then message, then the error itself', () => {
		expect(
			cancelErrorMessage({ response: { data: { detail: { error: 'cancel_failed', message: 'Backend refused.' } } } })
		).toBe('Backend refused.');
		expect(cancelErrorMessage({ response: { data: { detail: 'plain' } } })).toBe('plain');
		expect(cancelErrorMessage({ response: { data: { message: 'top level' } } })).toBe('top level');
		expect(cancelErrorMessage(new Error('boom'))).toBe('boom');
		expect(cancelErrorMessage({})).toBe('Could not stop this generation.');
	});
});

describe('settleStopping', () => {
	it('keeps Stopping only for generations still listed', () => {
		expect(settleStopping({ a: true, b: true }, [{ id: 'b' }, { id: 'c' }])).toEqual({ b: true });
	});
});
