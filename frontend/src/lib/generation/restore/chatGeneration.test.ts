import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { findTabByGenerationId } from '$lib/stores/generation';
import { attachChatStartedGeneration } from './chatGeneration';
import { resetPendingPosterRecoveriesForTests, type ReconcileApi } from './reconcile';
import type { APIResponse, GenerationStatus } from '$lib/types/api';

function defaultTabId(): string {
	return get(tabsStore).tabs[0].id;
}

function currentTab(tabId: string) {
	return get(tabsStore).tabs.find((t) => t.id === tabId)!;
}

function statusResponse(
	overrides: Partial<GenerationStatus> & { status: GenerationStatus['status'] }
): APIResponse<GenerationStatus> {
	return {
		success: true,
		data: {
			id: overrides.id ?? 'gen-1',
			created_at: '2026-01-01T00:00:00Z',
			...overrides
		} as GenerationStatus
	};
}

function createFakeApi(statusById: Record<string, APIResponse<GenerationStatus>>): ReconcileApi {
	return {
		async getGenerationStatus(id: string) {
			const response = statusById[id];
			if (!response) throw new Error(`no scripted status response for ${id}`);
			return response;
		},
		async getGenerationById() {
			return { success: true, data: { files: [] } };
		}
	};
}

describe('attachChatStartedGeneration', () => {
	beforeEach(() => {
		tabsStore.reset();
		resetPendingPosterRecoveriesForTests();
	});

	it('registers a running chat-started generation on its target tab and subscribes to it', async () => {
		const tabId = defaultTabId();
		const api = createFakeApi({
			'gen-chat-1': statusResponse({ status: 'running', id: 'gen-chat-1' })
		});
		const onSubscribe = vi.fn();
		const unsubscribe = vi.fn();

		expect(findTabByGenerationId('gen-chat-1')).toBeNull();

		await attachChatStartedGeneration(
			{ generation_id: 'gen-chat-1', tab_id: tabId, status: 'pending' },
			{ api, tabsStore, subscriptionOwner: {}, onSubscribe, unsubscribe }
		);

		expect(findTabByGenerationId('gen-chat-1')).toBe(tabId);
		expect(onSubscribe).toHaveBeenCalledWith('gen-chat-1');
		const tab = currentTab(tabId);
		expect(tab.generation.queue).toEqual(
			expect.arrayContaining([
				expect.objectContaining({ generation_id: 'gen-chat-1', status: 'running' })
			])
		);
	});

	it('does nothing when the targeted tab no longer exists', async () => {
		const api = createFakeApi({
			'gen-chat-2': statusResponse({ status: 'running', id: 'gen-chat-2' })
		});
		const onSubscribe = vi.fn();
		const unsubscribe = vi.fn();

		await attachChatStartedGeneration(
			{ generation_id: 'gen-chat-2', tab_id: 'tab-does-not-exist', status: 'pending' },
			{ api, tabsStore, subscriptionOwner: {}, onSubscribe, unsubscribe }
		);

		expect(findTabByGenerationId('gen-chat-2')).toBeNull();
		expect(onSubscribe).not.toHaveBeenCalled();
	});

	it('is a no-op when the result carries no generation_id or tab_id', async () => {
		const api = createFakeApi({});
		const onSubscribe = vi.fn();
		const unsubscribe = vi.fn();

		await attachChatStartedGeneration(
			{ message: 'Generation started successfully' },
			{ api, tabsStore, subscriptionOwner: {}, onSubscribe, unsubscribe }
		);

		expect(onSubscribe).not.toHaveBeenCalled();
	});
});
