import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { dispatchGenerationMessage } from '$lib/stores/generation';

// Importing '$lib/stores/generation' pulls in '$lib/generation/messages' as a
// side effect, which registers the queue_update handler under test.

function defaultTabId(): string {
	return get(tabsStore).tabs[0].id;
}

function seedQueue(tabId: string, queue: { generation_id: string; queue_position: number | null; status: 'pending' | 'running' }[]) {
	const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
	tabsStore.updateTab(tabId, { generation: { ...tab.generation, queue } });
}

describe('queue_update message handler', () => {
	beforeEach(() => tabsStore.reset());

	it('adds a new entry to the owning tab queue when the generation was not tracked yet', () => {
		const tabId = defaultTabId();
		seedQueue(tabId, [{ generation_id: 'gen-1', queue_position: 0, status: 'running' }]);

		dispatchGenerationMessage(
			{
				type: 'queue_update',
				generation_id: 'gen-1',
				tab_id: tabId,
				status: 'running',
				queue_position: null
			} as any,
			{ unsubscribe: vi.fn() }
		);

		const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		expect(tab.generation.queue).toEqual([
			{ generation_id: 'gen-1', queue_position: null, status: 'running' }
		]);
	});

	it('updates the matching entry in place, preserving other queued entries', () => {
		const tabId = defaultTabId();
		seedQueue(tabId, [
			{ generation_id: 'gen-a', queue_position: 0, status: 'running' },
			{ generation_id: 'gen-b', queue_position: 3, status: 'pending' }
		]);

		dispatchGenerationMessage(
			{
				type: 'queue_update',
				generation_id: 'gen-b',
				tab_id: tabId,
				status: 'pending',
				queue_position: 1
			} as any,
			{ unsubscribe: vi.fn() }
		);

		const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		expect(tab.generation.queue).toEqual([
			{ generation_id: 'gen-a', queue_position: 0, status: 'running' },
			{ generation_id: 'gen-b', queue_position: 1, status: 'pending' }
		]);
	});

	it('transitions an entry from pending to running with a null queue_position', () => {
		const tabId = defaultTabId();
		seedQueue(tabId, [{ generation_id: 'gen-c', queue_position: 0, status: 'pending' }]);

		dispatchGenerationMessage(
			{
				type: 'queue_update',
				generation_id: 'gen-c',
				tab_id: tabId,
				status: 'running',
				queue_position: null
			} as any,
			{ unsubscribe: vi.fn() }
		);

		const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		expect(tab.generation.queue).toEqual([
			{ generation_id: 'gen-c', queue_position: null, status: 'running' }
		]);
	});

	it('routes to the tab that owns the generation via generation.queue, not just currentGeneration', () => {
		tabsStore.addTab();
		const [firstTabId, secondTabId] = get(tabsStore).tabs.map((t) => t.id);
		seedQueue(secondTabId, [{ generation_id: 'gen-second-tab', queue_position: 2, status: 'pending' }]);

		dispatchGenerationMessage(
			{
				type: 'queue_update',
				generation_id: 'gen-second-tab',
				tab_id: secondTabId,
				status: 'pending',
				queue_position: 1
			} as any,
			{ unsubscribe: vi.fn() }
		);

		const tabs = get(tabsStore).tabs;
		const first = tabs.find((t) => t.id === firstTabId)!;
		const second = tabs.find((t) => t.id === secondTabId)!;
		expect(first.generation.queue).toEqual([]);
		expect(second.generation.queue).toEqual([
			{ generation_id: 'gen-second-tab', queue_position: 1, status: 'pending' }
		]);
	});
});
