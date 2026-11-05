import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';

const memory = new Map<string, string>();
vi.stubGlobal('localStorage', {
	getItem: (key: string) => memory.get(key) ?? null,
	setItem: (key: string, value: string) => void memory.set(key, value),
	removeItem: (key: string) => void memory.delete(key)
});

const { tabsStore } = await import('$lib/stores/tabs');
const { findGenerationTab } = await import('$lib/types/tabs');
const { dispatchGenerationMessage } = await import('$lib/stores/generation');
const { reconcileTabGenerations } = await import('$lib/generation/restore/reconcile');
const { setGenerationAttachHost, attachExistingGenerations } = await import('$lib/generation/restore/attachHost');
const { setGenerationUnsubscribeHandler } = await import('$lib/generation/messages/generationOutputs');
const { createStepLive, stepGenerations } = await import('./stepLive');
import type { Invocation, Run } from './types';

function invocation(over: Partial<Invocation> & { generation_id: string }): Invocation {
	return { node_id: 'n1', status: 'running', input_key: over.generation_id, attempt: 1, ordinal: 0, ...over };
}

function runOf(...items: Invocation[]): Run {
	return { id: 'run-1', status: 'running', invocations: items };
}

const files: Record<string, unknown[]> = {
	'g-done-1': [{ file_type: 'IMAGE', file_path: 'a/0.png' }],
	'g-done-2': [
		{ file_type: 'IMAGE', file_path: 'b/0.png' },
		{ file_type: 'IMAGE', file_path: 'b/1.png' }
	]
};

const statuses: Record<string, string> = {};
const subscribed: string[] = [];
const unsubscribed: string[] = [];
let owner: object = {};

const fakeApi = {
	getGenerationStatus: async (id: string) => ({ success: true, data: { id, status: statuses[id] ?? 'running', created_at: '2026-01-01T00:00:00Z', message: 'boom' } }),
	getGenerationById: async (id: string) => ({ success: true, data: { files: files[id] ?? [] } })
} as never;

function liveFor(tabId: string) {
	return createStepLive({
		readTab: () => findGenerationTab(get(tabsStore).tabs, tabId),
		updateTab: (patch) => tabsStore.updateTab(tabId, patch),
		attach: (ids) => attachExistingGenerations(tabId, ids),
		files: async (generationId) => (files[generationId] as unknown[]) ?? null
	});
}

function tab(tabId: string) {
	return findGenerationTab(get(tabsStore).tabs, tabId)!;
}

describe('stepGenerations', () => {
	it('lists the newest invocation first, the latest attempt per input, without superseded or id-less rows', () => {
		const run = runOf(
			invocation({ generation_id: 'old', input_key: 'a', attempt: 1, status: 'failed', submitted_at: '2026-01-01T00:00:01Z' }),
			invocation({ generation_id: 'retry', input_key: 'a', attempt: 2, status: 'completed', submitted_at: '2026-01-01T00:00:05Z' }),
			invocation({ generation_id: 'b', input_key: 'b', status: 'running', submitted_at: '2026-01-01T00:00:09Z' }),
			invocation({ generation_id: 'sup', input_key: 'c', status: 'superseded', submitted_at: '2026-01-01T00:00:10Z' }),
			invocation({ generation_id: 'other', node_id: 'n2', submitted_at: '2026-01-01T00:00:11Z' }),
			{ node_id: 'n1', status: 'pending', input_key: 'd', attempt: 1, ordinal: 3 }
		);
		expect(stepGenerations(run, 'n1').map((item) => [item.id, item.status])).toEqual([
			['b', 'running'],
			['retry', 'completed']
		]);
		expect(stepGenerations(null, 'n1')).toEqual([]);
	});
});

describe('step live attach', () => {
	let tabId: string;

	beforeEach(() => {
		tabsStore.reset();
		tabId = get(tabsStore).tabs.find((item) => item.kind === 'generation')!.id;
		for (const key of Object.keys(statuses)) delete statuses[key];
		owner = {};
		subscribed.length = 0;
		unsubscribed.length = 0;
		setGenerationUnsubscribeHandler((id) => unsubscribed.push(id));
		setGenerationAttachHost((id, ids) =>
			reconcileTabGenerations(id, fakeApi, tabsStore, {
				extraCandidateIds: ids,
				subscriptionOwner: owner,
				onSubscribe: (generationId) => subscribed.push(generationId),
				unsubscribe: (generationId) => unsubscribed.push(generationId),
				retryDelayMs: 0
			})
		);
	});

	it('attaches a running generation so socket messages drive the workbench', async () => {
		statuses['g-run'] = 'running';
		await liveFor(tabId).sync([{ id: 'g-run', status: 'running', inputKey: 'k', at: 1 }]);

		expect(tab(tabId).activeGenerationId).toBe('g-run');
		expect(tab(tabId).generation.isGenerating).toBe(true);
		expect(subscribed).toEqual(['g-run']);

		dispatchGenerationMessage({ type: 'generation_status', generation_id: 'g-run', status: 'running', progress: 42 } as never, { unsubscribe: () => {} });
		expect((tab(tabId).generation.currentProgress as { progress: number }).progress).toBe(42);

		dispatchGenerationMessage(
			{ type: 'workbench_update', generation_id: 'g-run', pipe_id: 1, pipe_name: 'generator', output_type: 'image', file_type: 'image', temporary: true, image: 'data:image/png;base64,AAAA' } as never,
			{ unsubscribe: () => {} }
		);
		expect(tab(tabId).generation.currentGeneration?.current_image).toBe('data:image/png;base64,AAAA');
	});

	it('shows a generation that finished while attached as a normal completion', async () => {
		statuses['g-run'] = 'running';
		await liveFor(tabId).sync([{ id: 'g-run', status: 'running', inputKey: 'k', at: 1 }]);
		dispatchGenerationMessage({ type: 'generation_complete', generation_id: 'g-run', status: 'completed' } as never, { unsubscribe: (id) => unsubscribed.push(id) });
		expect(tab(tabId).generation.isGenerating).toBe(false);
		expect(unsubscribed).toContain('g-run');
	});

	it('shows a failed generation like a normal tab does', async () => {
		statuses['g-bad'] = 'failed';
		await liveFor(tabId).sync([{ id: 'g-bad', status: 'failed', inputKey: 'k', at: 1 }]);
		expect(tab(tabId).generation.isGenerating).toBe(false);
		expect(tab(tabId).generation.currentGeneration?.status).toBe('failed');
		expect(tab(tabId).generation.currentGeneration?.message).toBe('boom');
		expect(subscribed).toEqual([]);
	});

	it('hydrates finished results without regenerating or subscribing', async () => {
		await liveFor(tabId).sync([{ id: 'g-done-1', status: 'completed', inputKey: 'k', at: 1 }]);
		const generation = tab(tabId).generation;
		expect(generation.isGenerating).toBe(false);
		expect(generation.batchImages.map((image) => image.url)).toEqual(['/api/media/generations/g-done-1/0.png']);
		expect(generation.currentGeneration?.status).toBe('completed');
		expect(subscribed).toEqual([]);
	});

	it('merges several finished invocations, newest first', async () => {
		await liveFor(tabId).sync([
			{ id: 'g-done-2', status: 'completed', inputKey: 'b', at: 2 },
			{ id: 'g-done-1', status: 'completed', inputKey: 'a', at: 1 }
		]);
		const generation = tab(tabId).generation;
		expect(generation.batchImages.map((image) => image.url)).toEqual([
			'/api/media/generations/g-done-2/0.png',
			'/api/media/generations/g-done-2/1.png',
			'/api/media/generations/g-done-1/0.png'
		]);
		expect(generation.workbenchTotal).toBe(3);
	});

	it('routes several running invocations to one tab, newest as the display', async () => {
		statuses['g-a'] = 'running';
		statuses['g-b'] = 'running';
		await liveFor(tabId).sync([
			{ id: 'g-b', status: 'running', inputKey: 'b', at: 2 },
			{ id: 'g-a', status: 'running', inputKey: 'a', at: 1 }
		]);
		expect(tab(tabId).activeGenerationId).toBe('g-b');
		expect(tab(tabId).generation.queue.map((entry) => entry.generation_id).sort()).toEqual(['g-a', 'g-b']);
		expect([...subscribed].sort()).toEqual(['g-a', 'g-b']);
	});

	it('attaches an invocation that starts later and ignores ones already attached', async () => {
		statuses['g-a'] = 'running';
		statuses['g-b'] = 'running';
		const live = liveFor(tabId);
		await live.sync([{ id: 'g-a', status: 'running', inputKey: 'a', at: 1 }]);
		await live.sync([
			{ id: 'g-b', status: 'running', inputKey: 'b', at: 2 },
			{ id: 'g-a', status: 'running', inputKey: 'a', at: 1 }
		]);
		expect(tab(tabId).activeGenerationId).toBe('g-a');
		expect([...subscribed].sort()).toEqual(['g-a', 'g-b']);
	});

	it('releases subscriptions when the embedded tab goes away and leaves the generation alone', async () => {
		statuses['g-run'] = 'running';
		await liveFor(tabId).sync([{ id: 'g-run', status: 'running', inputKey: 'k', at: 1 }]);
		tabsStore.addTab();
		tabsStore.removeTab(tabId);
		expect(unsubscribed).toContain('g-run');
	});

	it('does not touch a normal test generation on the same tab afterwards', async () => {
		const live = liveFor(tabId);
		await live.sync([{ id: 'g-done-1', status: 'completed', inputKey: 'k', at: 1 }]);
		tabsStore.updateTab(tabId, { activeGenerationId: 'g-test', generation: { ...tab(tabId).generation, isGenerating: true, currentGeneration: { id: 'g-test', generation_id: 'g-test', status: 'running' }, queue: [{ generation_id: 'g-test', status: 'running', queue_position: null } as never] } });
		await live.sync([
			{ id: 'g-done-2', status: 'completed', inputKey: 'b', at: 2 },
			{ id: 'g-done-1', status: 'completed', inputKey: 'a', at: 1 }
		]);
		expect(tab(tabId).activeGenerationId).toBe('g-test');
		expect(tab(tabId).generation.queue.map((entry) => entry.generation_id)).toEqual(['g-test']);
		expect(tab(tabId).generation.isGenerating).toBe(true);
		dispatchGenerationMessage({ type: 'generation_status', generation_id: 'g-test', status: 'running', progress: 7 } as never, { unsubscribe: () => {} });
		expect((tab(tabId).generation.currentProgress as { progress: number }).progress).toBe(7);
	});

	it('keeps hydrated results and attaches the running one when both are present', async () => {
		statuses['g-run'] = 'running';
		await liveFor(tabId).sync([
			{ id: 'g-run', status: 'running', inputKey: 'r', at: 3 },
			{ id: 'g-done-1', status: 'completed', inputKey: 'a', at: 1 }
		]);
		expect(tab(tabId).generation.batchImages.map((image) => image.url)).toEqual(['/api/media/generations/g-done-1/0.png']);
		expect(tab(tabId).activeGenerationId).toBe('g-run');
		expect(subscribed).toEqual(['g-run']);
	});

	it('leaves the tab untouched when the finished files cannot be fetched', async () => {
		const before = tab(tabId);
		await liveFor(tabId).sync([{ id: 'g-missing', status: 'completed', inputKey: 'k', at: 1 }]);
		expect(tab(tabId)).toBe(before);
	});

	it('does not show a failed invocation over hydrated results', async () => {
		await liveFor(tabId).sync([
			{ id: 'g-bad', status: 'failed', inputKey: 'b', at: 2 },
			{ id: 'g-done-1', status: 'completed', inputKey: 'a', at: 1 }
		]);
		expect(tab(tabId).activeGenerationId).toBeNull();
		expect(tab(tabId).generation.batchImages).toHaveLength(1);
		expect(tab(tabId).generation.currentGeneration?.status).toBe('completed');
		expect(subscribed).toEqual([]);
	});

	it('retries attaching once a host is registered and marks nothing before that', async () => {
		statuses['g-run'] = 'running';
		setGenerationAttachHost(null);
		const live = liveFor(tabId);
		const items = [{ id: 'g-run', status: 'running' as const, inputKey: 'k', at: 1 }];
		await live.sync(items);
		expect(tab(tabId).activeGenerationId).toBeNull();
		expect(subscribed).toEqual([]);
		setGenerationAttachHost((id, ids) =>
			reconcileTabGenerations(id, fakeApi, tabsStore, {
				extraCandidateIds: ids,
				subscriptionOwner: owner,
				onSubscribe: (generationId) => subscribed.push(generationId),
				retryDelayMs: 0
			})
		);
		await live.sync(items);
		expect(tab(tabId).activeGenerationId).toBe('g-run');
		expect(subscribed).toEqual(['g-run']);
	});
});
