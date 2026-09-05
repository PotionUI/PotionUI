import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';

// Keep the module's public surface mockable — no real AudioContext in vitest.
vi.mock('$lib/utils/generationSounds', () => ({
	playGenerationCompleteSound: vi.fn(),
	playGenerationErrorSound: vi.fn(),
	unlockGenerationSoundContext: vi.fn()
}));

import { tabsStore } from '$lib/stores/tabs';
import { dispatchGenerationMessage } from '$lib/stores/generation';

// Importing '$lib/stores/generation' pulls in '$lib/generation/messages' as a
// side effect, which registers every handler under test.

function defaultTabId(): string {
	return get(tabsStore).tabs[0].id;
}

/** Seeds a tab with A as its owning/current generation and B enqueued
 *  alongside it (mirrors a second Generate click while A is still running:
 *  both land in `generation.queue`, only A occupies `activeGenerationId`/
 *  `currentGeneration`). */
function seedRunningWithQueuedSecond(tabId: string) {
	const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
	tabsStore.updateTab(tabId, {
		activeGenerationId: 'gen-a',
		generation: {
			...tab.generation,
			isGenerating: true,
			currentGeneration: { generation_id: 'gen-a', id: 'gen-a', status: 'running' },
			currentProgress: { type: 'generation_status', progress: 0.4 } as any,
			queue: [
				{ generation_id: 'gen-a', queue_position: null, status: 'running' },
				{ generation_id: 'gen-b', queue_position: 1, status: 'pending' }
			]
		}
	});
}

function currentTab(tabId: string) {
	return get(tabsStore).tabs.find((t) => t.id === tabId)!;
}

describe('generation message ownership — A running, B queued in the same tab', () => {
	beforeEach(() => tabsStore.reset());

	it('cancelling B leaves A running, untouched, and only removes B from the queue', () => {
		const tabId = defaultTabId();
		seedRunningWithQueuedSecond(tabId);
		const unsubscribe = vi.fn();

		dispatchGenerationMessage({ type: 'generation_cancelled', generation_id: 'gen-b' } as any, { unsubscribe });

		const tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBe('gen-a');
		expect(tab.generation.isGenerating).toBe(true);
		expect(tab.generation.currentGeneration).toMatchObject({ generation_id: 'gen-a', status: 'running' });
		expect(tab.generation.currentProgress).toMatchObject({ progress: 0.4 });
		expect(tab.generation.queue).toEqual([{ generation_id: 'gen-a', queue_position: null, status: 'running' }]);
		expect(unsubscribe).toHaveBeenCalledWith('gen-b');
	});

	it('B failing leaves A running, untouched, and only removes B from the queue', () => {
		const tabId = defaultTabId();
		seedRunningWithQueuedSecond(tabId);

		dispatchGenerationMessage(
			{ type: 'generation_error', generation_id: 'gen-b', error: 'boom' } as any,
			{ unsubscribe: vi.fn() }
		);

		const tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBe('gen-a');
		expect(tab.generation.isGenerating).toBe(true);
		expect(tab.generation.currentGeneration).toMatchObject({ generation_id: 'gen-a', status: 'running' });
		expect(tab.generation.queue).toEqual([{ generation_id: 'gen-a', queue_position: null, status: 'running' }]);
	});

	it('A failing while B is queued clears the shared display and leaves B queued', () => {
		const tabId = defaultTabId();
		seedRunningWithQueuedSecond(tabId);

		dispatchGenerationMessage(
			{ type: 'generation_error', generation_id: 'gen-a', error: 'boom' } as any,
			{ unsubscribe: vi.fn() }
		);

		const tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBeNull();
		expect(tab.generation.isGenerating).toBe(false);
		expect(tab.generation.currentGeneration).toMatchObject({ status: 'failed' });
		// No adoption mechanism promotes a queued run on a live terminal event
		// (only the page-reload restore path does) — B stays queued, not current.
		expect(tab.generation.queue).toEqual([{ generation_id: 'gen-b', queue_position: 1, status: 'pending' }]);
	});

	it("B's gallery_update does not become A's media, and A's completion still shows A's own output", () => {
		const tabId = defaultTabId();
		seedRunningWithQueuedSecond(tabId);

		dispatchGenerationMessage(
			{
				type: 'gallery_update',
				generation_id: 'gen-b',
				videos: [{ path: '/api/media/generations/gen-b/0.mp4' }],
				video_urls_list: [{ path: '/api/media/generations/gen-b/0.mp4' }]
			} as any,
			{ unsubscribe: vi.fn() }
		);

		let tab = currentTab(tabId);
		expect(tab.generation.batchVideos).toEqual([]);

		dispatchGenerationMessage(
			{
				type: 'gallery_update',
				generation_id: 'gen-a',
				videos: [{ path: '/api/media/generations/gen-a/0.mp4' }],
				video_urls_list: [{ path: '/api/media/generations/gen-a/0.mp4' }]
			} as any,
			{ unsubscribe: vi.fn() }
		);

		tab = currentTab(tabId);
		expect(tab.generation.batchVideos).toHaveLength(1);
		expect(tab.generation.batchVideos[0].originalUrl).toBe('/api/media/generations/gen-a/0.mp4');

		dispatchGenerationMessage({ type: 'generation_complete', data: { id: 'gen-a' } } as any, { unsubscribe: vi.fn() });

		tab = currentTab(tabId);
		expect(tab.generation.currentGeneration).toMatchObject({
			status: 'completed',
			file_type: 'video',
			current_video: '/api/media/generations/gen-a/0.mp4'
		});
		expect(tab.generation.workbenchTotal).toBe(1);
		// B is still queued, its own output never surfaced on the tab.
		expect(tab.generation.queue).toEqual([{ generation_id: 'gen-b', queue_position: 1, status: 'pending' }]);
	});
});
