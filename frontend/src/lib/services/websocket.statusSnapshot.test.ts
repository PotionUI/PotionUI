import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { WebSocketService, type WebSocketMessage } from './websocket';
import { tabsStore } from '$lib/stores/tabs';
import { dispatchGenerationMessage } from '$lib/stores/generation';
import { generationTabs } from '$lib/types/tabs';
import { reconcileTabGenerations } from '$lib/generation/restore/reconcile';

function socket() {
	const ws = new WebSocketService('/ws/generation');
	vi.spyOn(ws as never as { send: (m: object) => void }, 'send').mockImplementation(() => {});
	return ws;
}

const deliver = (ws: WebSocketService, message: object) => (ws as unknown as { onMessage(m: object): void }).onMessage(message);

const SNAPSHOT = {
	type: 'status_update',
	data: { id: 'g1', status: 'running', progress: 0.7, current_step: 'Sampling <<PROGRESS:21/30>>', message: 'x', current_step_num: 21, total_steps: 30, created_at: '2026-11-17T09:00:00Z' }
};

describe('the status snapshot the server sends right after subscribing', () => {
	it('reaches the generation handlers as a progress message', () => {
		const ws = socket();
		const handler = vi.fn();
		ws.subscribe('g1', handler);
		deliver(ws, SNAPSHOT);
		expect(handler).toHaveBeenCalledTimes(1);
		expect(handler.mock.calls[0][0]).toMatchObject({ type: 'generation_status', generation_id: 'g1', progress: 0.7, current_step_num: 21, total_steps: 30 });
	});

	it.each([
		['a finished generation', { ...SNAPSHOT.data, status: 'completed' }],
		['a snapshot without progress', { ...SNAPSHOT.data, progress: null }],
		['a snapshot for nothing subscribed', { ...SNAPSHOT.data, id: 'other' }]
	])('is ignored for %s', (_label, data) => {
		const ws = socket();
		const handler = vi.fn();
		ws.subscribe('g1', handler);
		deliver(ws, { type: 'status_update', data });
		expect(handler).not.toHaveBeenCalled();
	});
});

describe('an attached tab right after a reload', () => {
	beforeEach(() => tabsStore.reset());

	const api = () => ({
		getGenerationStatus: vi.fn(async () => ({ success: true, data: { id: 'g1', status: 'running', progress: 0.2, created_at: '2026-11-17T09:00:00Z' } }) as never),
		getGenerationById: vi.fn(async () => ({ success: true, data: { files: [] } }) as never)
	});

	it('shows the snapshot delivered the instant it subscribes, with no later progress message', async () => {
		const tabId = generationTabs(get(tabsStore).tabs)[0].id;
		tabsStore.updateTab(tabId, { activeGenerationId: 'g1' });
		const ws = socket();
		await reconcileTabGenerations(tabId, api(), tabsStore, {
			subscriptionOwner: ws,
			onSubscribe: (id) => {
				ws.subscribe(id, (message: WebSocketMessage) => dispatchGenerationMessage(message as never, { unsubscribe: vi.fn() }));
				deliver(ws, SNAPSHOT);
			}
		});
		const tab = generationTabs(get(tabsStore).tabs)[0];
		expect(tab.generation.isGenerating).toBe(true);
		expect((tab.generation.currentProgress as { progress?: number } | null)?.progress).toBe(0.7);
	});
});
