import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { get } from 'svelte/store';

vi.mock('./compareApi', () => ({
	postGrid: vi.fn(),
	fetchGrid: vi.fn(),
	postRetryFailed: vi.fn(),
	removeGrid: vi.fn(),
	fetchGridSettings: vi.fn(async () => ({ confirm_above: 24, hard_cap: 100 }))
}));

import { tabsStore } from '$lib/stores/tabs';
import { toPersistedTab } from '$lib/stores/tabPersistence';
import { buildSessionRestoreTabPatch } from '$lib/utils/sessionRestore';
import { collectTabSessionData } from '$lib/utils/sessionTabState';
import { toasts } from '$lib/stores/toast';
import * as api from './compareApi';
import { api as serviceApi } from '$lib/services/api/index';
import {
	axisRoleFor,
	cancelGrid,
	cellCount,
	clearAxes,
	effectiveLockSeed,
	getActiveGrid,
	getCompare,
	isCompareActive,
	isGridRunning,
	loadGrid,
	observeGenerationMessage,
	publishCompareSchema,
	readWorkbenchGrid,
	resetCompareStoreForTests,
	retryFailed,
	setAxis,
	setCompare,
	setCompareBlocked,
	setGridRunHandlers,
	submitGrid,
	swapAxes
} from './compareStore.svelte';
import type { CompareAxis, ServerGrid } from './types';

const sampler: CompareAxis = {
	field: 'sampler',
	type: 'select',
	label: 'Sampler',
	values: ['euler', 'er_sde', 'dpmpp_2m', 'heun'].map((v) => ({ value: v, label: v }))
};
const scheduler: CompareAxis = {
	field: 'scheduler',
	type: 'select',
	label: 'Scheduler',
	values: ['simple', 'beta', 'karras'].map((v) => ({ value: v, label: v }))
};

let tabId = '';

function serverGrid(overrides: Partial<ServerGrid> = {}): ServerGrid {
	const cells: ServerGrid['cells'] = [];
	let n = 0;
	for (let y = 0; y < 3; y++) {
		for (let x = 0; x < 4; x++) {
			cells.push({
				x,
				y,
				generation_id: `gen-${n++}`,
				status: n === 1 ? 'running' : 'queued',
				axis_values: { sampler: sampler.values[x].label, scheduler: scheduler.values[y].label },
				seed: 7,
				thumbnail_url: null,
				media_type: null,
				error: null
			});
		}
	}
	return {
		id: 'grid-1',
		preset_id: 'native/Krea2',
		tab_id: tabId,
		x_axis: sampler,
		y_axis: scheduler,
		lock_seed: true,
		status: 'running',
		created_at: '2026-10-05T10:00:00Z',
		cells,
		...overrides
	};
}

async function startGrid() {
	setCompare(tabId, { armed: true, x: sampler, y: scheduler });
	vi.mocked(api.postGrid).mockResolvedValueOnce(serverGrid());
	const result = await submitGrid(tabId, { preset_id: 'native/Krea2' } as never);
	expect(result.ok).toBe(true);
	return result;
}

beforeEach(() => {
	tabsStore.reset();
	resetCompareStoreForTests();
	tabId = get(tabsStore).tabs[0].id;
	tabsStore.updateTab(tabId, { selectedPreset: 'native/Krea2' });
	vi.mocked(api.postGrid).mockReset();
	vi.mocked(api.fetchGrid).mockReset();
});

afterEach(() => {
	vi.useRealTimers();
});

describe('cell count and axes', () => {
	it('multiplies the two axes and counts a single axis as one row', () => {
		setCompare(tabId, { armed: true, x: sampler, y: scheduler });
		expect(cellCount(getCompare(tabId))).toBe(12);
		setCompare(tabId, { y: null });
		expect(cellCount(getCompare(tabId))).toBe(4);
		setCompare(tabId, { x: null, y: scheduler });
		expect(cellCount(getCompare(tabId))).toBe(3);
		clearAxes(tabId);
		expect(cellCount(getCompare(tabId))).toBe(0);
	});

	it('swaps X and Y with their values and clears both', () => {
		setCompare(tabId, { armed: true, x: sampler, y: scheduler });
		swapAxes(tabId);
		expect(getCompare(tabId).x?.field).toBe('scheduler');
		expect(getCompare(tabId).y?.field).toBe('sampler');
		expect(getCompare(tabId).y?.values).toHaveLength(4);
		clearAxes(tabId);
		expect(getCompare(tabId).x).toBeNull();
		expect(getCompare(tabId).y).toBeNull();
		expect(getCompare(tabId).armed).toBe(true);
	});

	it('moves a field off the other axis when it is picked twice', () => {
		setAxis(tabId, 'x', sampler);
		setAxis(tabId, 'y', { ...sampler, values: sampler.values.slice(0, 2) });
		expect(getCompare(tabId).x).toBeNull();
		expect(getCompare(tabId).y?.field).toBe('sampler');
	});

	it('forces Lock seed off while seed is an axis but remembers the preference', () => {
		setCompare(tabId, { armed: true, x: { field: 'seed', type: 'seed', label: 'Seed', values: [{ value: 1, label: '1' }] } });
		expect(getCompare(tabId).lockSeed).toBe(true);
		expect(effectiveLockSeed(getCompare(tabId))).toBe(false);
		setAxis(tabId, 'x', sampler);
		expect(effectiveLockSeed(getCompare(tabId))).toBe(true);
	});

	it('reports the axis role of a field only while armed and unblocked', () => {
		setCompare(tabId, { armed: true, x: sampler, y: scheduler });
		expect(axisRoleFor(tabId, 'sampler')).toEqual({ role: 'x', count: 4 });
		expect(axisRoleFor(tabId, 'scheduler')).toEqual({ role: 'y', count: 3 });
		expect(axisRoleFor(tabId, 'steps')).toBeNull();
		setCompareBlocked(tabId, 'Not here');
		expect(axisRoleFor(tabId, 'sampler')).toBeNull();
		expect(isCompareActive(tabId)).toBe(false);
		setCompareBlocked(tabId, null);
		setCompare(tabId, { armed: false });
		expect(axisRoleFor(tabId, 'sampler')).toBeNull();
	});
});

describe('preview grid', () => {
	it('is null until armed and then reflects the axes as empty cells', () => {
		setCompare(tabId, { x: sampler, y: scheduler });
		expect(getActiveGrid(tabId)).toBeNull();
		setCompare(tabId, { armed: true });
		const grid = getActiveGrid(tabId)!;
		expect(grid.id).toBeNull();
		expect(grid.cols).toBe(4);
		expect(grid.rows).toBe(3);
		expect(grid.cells).toHaveLength(12);
		expect(grid.cells.every((cell) => cell.status === 'empty')).toBe(true);
		expect(grid.cells[5]).toMatchObject({ x: 1, y: 1, axisValues: { sampler: 'er_sde', scheduler: 'beta' } });
	});

	it('shows one row for a single axis and nothing without values', () => {
		setCompare(tabId, { armed: true, x: sampler });
		expect(getActiveGrid(tabId)).toMatchObject({ cols: 4, rows: 1 });
		clearAxes(tabId);
		expect(getActiveGrid(tabId)).toBeNull();
	});
});

describe('live grid and message mapping', () => {
	it('submits the grid, adopts the cells and enrols them in the tab queue', async () => {
		const result = await startGrid();
		expect(vi.mocked(api.postGrid)).toHaveBeenCalledWith(
			expect.objectContaining({ x_axis: sampler, y_axis: scheduler, lock_seed: true })
		);
		if (!result.ok) return;
		expect(result.generationIds).toHaveLength(12);
		const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		expect(tab.generation.queue).toHaveLength(12);
		expect(tab.generation.isGenerating).toBe(true);
		expect(tab.compareGridId).toBe('grid-1');
		expect(getActiveGrid(tabId)?.id).toBe('grid-1');
		expect(isGridRunning(tabId)).toBe(true);
	});

	it('maps status, preview, gallery and terminal messages onto the right cell', async () => {
		vi.useFakeTimers();
		await startGrid();
		vi.mocked(api.fetchGrid).mockResolvedValue(serverGrid());

		observeGenerationMessage({ type: 'generation_status', generation_id: 'gen-1', status: 'running', current_step_num: 14, total_steps: 28 });
		let cell = getActiveGrid(tabId)!.cells[1];
		expect(cell.status).toBe('running');
		expect(cell.progress).toEqual({ step: 14, total: 28 });

		observeGenerationMessage({ type: 'workbench_update', generation_id: 'gen-1', image: '/api/media/preview.png' });
		cell = getActiveGrid(tabId)!.cells[1];
		expect(cell.previewUrl).toBe('/api/media/preview.png');

		observeGenerationMessage({
			type: 'gallery_update',
			generation_id: 'gen-1',
			images: ['/api/media/final.png'],
			image_urls_list: [{ original: '/api/media/final.png' }]
		});
		cell = getActiveGrid(tabId)!.cells[1];
		expect(cell.thumbnailUrl).toBe('/api/media/final.png');
		expect(cell.mediaType).toBe('image');

		observeGenerationMessage({ type: 'generation_complete', data: { id: 'gen-1', status: 'completed' } });
		cell = getActiveGrid(tabId)!.cells[1];
		expect(cell.status).toBe('completed');
		expect(cell.progress).toBeNull();
		expect(cell.previewUrl).toBeNull();
		expect(getActiveGrid(tabId)!.cells[0].status).toBe('running');
		expect(getActiveGrid(tabId)!.cells[2].status).toBe('queued');
	});

	it('marks a failed cell with its plain message and a cancelled cell without one', async () => {
		vi.useFakeTimers();
		await startGrid();
		vi.mocked(api.fetchGrid).mockResolvedValue(serverGrid());
		observeGenerationMessage({ type: 'generation_error', generation_id: 'gen-3', message: 'Ran out of memory' });
		observeGenerationMessage({ type: 'generation_cancelled', data: { id: 'gen-4' } });
		const cells = getActiveGrid(tabId)!.cells;
		expect(cells[3]).toMatchObject({ status: 'failed', error: 'Ran out of memory' });
		expect(cells[4]).toMatchObject({ status: 'cancelled', error: null });
	});

	it('ignores messages for generations it does not own', async () => {
		await startGrid();
		expect(observeGenerationMessage({ type: 'generation_status', generation_id: 'someone-else', status: 'running' })).toBe(false);
	});

	it('refreshes the grid from the server after a terminal message', async () => {
		vi.useFakeTimers();
		await startGrid();
		const done = serverGrid();
		done.cells[2] = { ...done.cells[2], status: 'completed', thumbnail_url: '/api/media/c.png', media_type: 'image' };
		vi.mocked(api.fetchGrid).mockResolvedValue(done);
		observeGenerationMessage({ type: 'generation_complete', data: { id: 'gen-2', status: 'completed' } });
		await vi.advanceTimersByTimeAsync(1000);
		expect(vi.mocked(api.fetchGrid)).toHaveBeenCalledWith('grid-1');
		expect(getActiveGrid(tabId)!.cells[2]).toMatchObject({ status: 'completed', thumbnailUrl: '/api/media/c.png' });
	});

	it('refuses a grid over the hard cap before calling the server', async () => {
		const wide: CompareAxis = {
			...sampler,
			values: Array.from({ length: 101 }, (_, i) => ({ value: i, label: String(i) }))
		};
		setCompare(tabId, { armed: true, x: wide });
		const result = await submitGrid(tabId, {} as never);
		expect(result).toMatchObject({ ok: false, reason: 'too_large' });
		expect(vi.mocked(api.postGrid)).not.toHaveBeenCalled();
	});

	it('surfaces a Plans shortfall from the server without queueing anything', async () => {
		setCompare(tabId, { armed: true, x: sampler, y: scheduler });
		vi.mocked(api.postGrid).mockRejectedValueOnce({
			response: { status: 403, data: { detail: { error: 'limit_exceeded', kind: 'generations_per_day', needed: 12, remaining: 5 } } }
		});
		const result = await submitGrid(tabId, {} as never);
		expect(result).toMatchObject({ ok: false, reason: 'refused', shortfall: { needed: 12, remaining: 5 } });
		expect(get(tabsStore).tabs.find((t) => t.id === tabId)!.generation.queue).toHaveLength(0);
	});
});

describe('run handlers and the workbench grid', () => {
	it('hands submitted cells to the enrolled handler and confirmed cancels to the cancelled one', async () => {
		const enrolled = vi.fn();
		const cancelled = vi.fn();
		setGridRunHandlers({ enrolled, cancelled });
		await startGrid();
		expect(enrolled).toHaveBeenCalledTimes(1);
		expect(enrolled.mock.calls[0][0]).toBe(tabId);
		expect(enrolled.mock.calls[0][1]).toHaveLength(12);

		const clear = vi
			.spyOn(serviceApi, 'clearGenerationQueue')
			.mockResolvedValue({ success: true, data: { cancelled: ['gen-1', 'gen-2'] } } as never);
		const cancel = vi.spyOn(serviceApi, 'cancelGeneration').mockResolvedValue({ success: true } as never);
		try {
			await cancelGrid(tabId);
		} finally {
			clear.mockRestore();
			cancel.mockRestore();
		}
		expect(cancelled).toHaveBeenCalledTimes(1);
		expect([...cancelled.mock.calls[0][1]].sort()).toEqual(['gen-0', 'gen-1', 'gen-2']);
	});

	it('hands retried cells to the same enrolled handler', async () => {
		await startGrid();
		const enrolled = vi.fn();
		setGridRunHandlers({ enrolled, cancelled: vi.fn() });
		const retried = serverGrid();
		retried.cells[11] = { ...retried.cells[11], generation_id: 'gen-retry', status: 'queued' };
		vi.mocked(api.postRetryFailed).mockResolvedValueOnce(retried);
		expect(await retryFailed(tabId)).toEqual(['gen-retry']);
		expect(enrolled).toHaveBeenCalledWith(tabId, ['gen-retry']);
	});

	it('keeps a finished grid on the workbench only while Compare is armed, and a running one regardless', async () => {
		await startGrid();
		setCompare(tabId, { armed: false });
		expect(readWorkbenchGrid(0, tabId)?.id).toBe('grid-1');

		const done = serverGrid();
		done.cells = done.cells.map((cell) => ({ ...cell, status: 'completed', elapsed_seconds: 3.5 }));
		vi.mocked(api.fetchGrid).mockResolvedValueOnce(done);
		await loadGrid(tabId, 'grid-1');
		expect(getActiveGrid(tabId)?.cells[0].elapsedSeconds).toBe(3.5);
		expect(readWorkbenchGrid(0, tabId)).toBeNull();

		setCompare(tabId, { armed: true });
		expect(readWorkbenchGrid(0, tabId)?.id).toBe('grid-1');
	});
});

describe('persistence', () => {
	it('round-trips through the persisted tab, the session payload and the restore patch', () => {
		setCompare(tabId, { armed: true, x: sampler, y: scheduler, lockSeed: false });
		const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;

		const persisted = JSON.parse(JSON.stringify(toPersistedTab(tab)));
		expect(persisted.compare).toMatchObject({ armed: true, lockSeed: false });
		expect(persisted.compare.x.values).toHaveLength(4);

		const session = collectTabSessionData(tab, 'txt2img');
		expect(session.txt2img.compare?.y?.field).toBe('scheduler');

		const patch = buildSessionRestoreTabPatch(session.txt2img);
		expect(patch.compare?.x?.field).toBe('sampler');
		tabsStore.updateTab(tabId, { compare: undefined });
		expect(getCompare(tabId).armed).toBe(false);
		tabsStore.updateTab(tabId, patch);
		expect(getCompare(tabId)).toMatchObject({ armed: true, lockSeed: false });
		expect(cellCount(getCompare(tabId))).toBe(12);
	});

	it('leaves a session without a comparison out of the payload', () => {
		const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		expect(collectTabSessionData(tab, 'txt2img').txt2img.compare).toBeUndefined();
	});

	it('rehydrates from localStorage on a fresh module load', async () => {
		const store = new Map<string, string>();
		(globalThis as any).localStorage = {
			getItem: (key: string) => store.get(key) ?? null,
			setItem: (key: string, value: string) => void store.set(key, value),
			removeItem: (key: string) => void store.delete(key),
			clear: () => store.clear()
		};
		store.set(
			'potionui_tabs_state',
			JSON.stringify({
				tabs: [
					{
						id: 'saved-1',
						name: 'Generation 1',
						selectedPreset: null,
						selectedMode: null,
						selectedSessionId: null,
						activeGenerationId: null,
						compare: { armed: true, x: sampler, y: null, lockSeed: true }
					}
				],
				activeTabId: 'saved-1'
			})
		);
		vi.doMock('$app/environment', () => ({ browser: true }));
		vi.resetModules();
		try {
			await import('$lib/stores/tabs');
			const fresh = await import('./compareStore.svelte');
			expect(fresh.getCompare('saved-1').armed).toBe(true);
			expect(fresh.cellCount(fresh.getCompare('saved-1'))).toBe(4);
		} finally {
			vi.doUnmock('$app/environment');
			vi.resetModules();
			delete (globalThis as any).localStorage;
		}
	});
});

describe('preset switch', () => {
	const schemaWith = (names: string[]) => ({
		properties: {
			tabs: {
				type: 'tabs',
				children: [
					{
						type: 'tab',
						label: 'Generation',
						children: names.map((name) => ({
							type: 'select',
							name,
							title: name,
							options: [
								{ value: 'a', label: 'a' },
								{ value: 'b', label: 'b' }
							]
						}))
					}
				]
			}
		}
	});

	it('drops axes whose field is missing from the new preset, toasts, and stays armed', () => {
		const info = vi.spyOn(toasts, 'info');
		setCompare(tabId, { armed: true, x: sampler, y: scheduler });
		publishCompareSchema(tabId, 'preset-a-txt2img-', schemaWith(['sampler', 'scheduler']));
		expect(getCompare(tabId).x?.field).toBe('sampler');
		expect(getCompare(tabId).y?.field).toBe('scheduler');
		expect(info).not.toHaveBeenCalled();

		publishCompareSchema(tabId, 'preset-b-txt2img-', schemaWith(['sampler']));
		expect(getCompare(tabId).x?.field).toBe('sampler');
		expect(getCompare(tabId).y).toBeNull();
		expect(getCompare(tabId).armed).toBe(true);
		expect(info).toHaveBeenCalledTimes(1);
		expect(String(info.mock.calls[0][0])).toContain('Scheduler');
	});

	it('keeps the synthetic prompt axis across presets and does not re-reconcile the same key', () => {
		const prompt: CompareAxis = {
			field: '__prompt__',
			type: 'prompt',
			label: 'Prompt: find and replace',
			values: [{ value: { find: 'dusk', replace: null }, label: 'dusk' }]
		};
		setCompare(tabId, { armed: true, x: prompt });
		publishCompareSchema(tabId, 'preset-c-txt2img-', schemaWith(['sampler']));
		expect(getCompare(tabId).x?.field).toBe('__prompt__');
	});
});
