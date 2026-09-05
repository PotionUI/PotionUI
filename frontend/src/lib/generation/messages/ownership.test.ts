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

/** Mirrors the Generate call site's "newest submission always claims
 *  activeGenerationId" rule: B is the tab's current display even though A,
 *  submitted earlier, is still confirmed running in the background. */
function seedNewestSubmissionOverOlderRunning(tabId: string) {
	const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
	tabsStore.updateTab(tabId, {
		activeGenerationId: 'gen-b',
		generation: {
			...tab.generation,
			isGenerating: true,
			currentGeneration: { generation_id: 'gen-b', id: 'gen-b', status: 'running' },
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

	it('A completing while B is only queued adopts B as the new owner, cold', () => {
		const tabId = defaultTabId();
		seedRunningWithQueuedSecond(tabId);

		dispatchGenerationMessage({ type: 'generation_complete', data: { id: 'gen-a' } } as any, { unsubscribe: vi.fn() });

		const tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBe('gen-b');
		expect(tab.generation.isGenerating).toBe(true);
		expect(tab.generation.currentGeneration).toEqual({ id: 'gen-b', generation_id: 'gen-b', status: 'running' });
		expect(tab.generation.currentProgress).toBeNull();
		expect(tab.generation.batchVideos).toEqual([]);
		expect(tab.generation.workbenchIndex).toBe(0);
		// B's own queue entry is untouched by adoption -- still whatever the
		// backend last reported (queue_update drives that, separately).
		expect(tab.generation.queue).toEqual([{ generation_id: 'gen-b', queue_position: 1, status: 'pending' }]);
	});

	it("B's gallery_update does not become A's media while A is running", () => {
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
		expect(tab.activeGenerationId).toBe('gen-a');
	});

	it('A completes, then B (still queued) becomes owner and its own events drive the display end to end', () => {
		const tabId = defaultTabId();
		seedRunningWithQueuedSecond(tabId);

		// A finishes -- B (the only remaining queue entry) is adopted cold.
		dispatchGenerationMessage({ type: 'generation_complete', data: { id: 'gen-a' } } as any, { unsubscribe: vi.fn() });
		let tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBe('gen-b');
		expect(tab.generation.currentGeneration).toMatchObject({ generation_id: 'gen-b', status: 'running' });

		// The backend confirms B is now actually running -- already owner, so
		// this only updates the queue entry itself.
		dispatchGenerationMessage(
			{ type: 'queue_update', generation_id: 'gen-b', status: 'running', queue_position: null } as any,
			{ unsubscribe: vi.fn() }
		);
		tab = currentTab(tabId);
		expect(tab.generation.queue).toEqual([{ generation_id: 'gen-b', queue_position: null, status: 'running' }]);
		expect(tab.activeGenerationId).toBe('gen-b');

		// B reports progress.
		dispatchGenerationMessage(
			{ type: 'generation_status', generation_id: 'gen-b', progress: 0.7 } as any,
			{ unsubscribe: vi.fn() }
		);
		tab = currentTab(tabId);
		expect(tab.generation.currentProgress).toMatchObject({ progress: 0.7 });

		// B's own output arrives.
		dispatchGenerationMessage(
			{
				type: 'gallery_update',
				generation_id: 'gen-b',
				videos: [{ path: '/api/media/generations/gen-b/0.mp4' }],
				video_urls_list: [{ path: '/api/media/generations/gen-b/0.mp4' }]
			} as any,
			{ unsubscribe: vi.fn() }
		);
		tab = currentTab(tabId);
		expect(tab.generation.batchVideos).toHaveLength(1);
		expect(tab.generation.batchVideos[0].originalUrl).toBe('/api/media/generations/gen-b/0.mp4');

		// B completes -- its own output is the final display, A's is long gone,
		// and nothing is left queued to adopt next.
		dispatchGenerationMessage({ type: 'generation_complete', data: { id: 'gen-b' } } as any, { unsubscribe: vi.fn() });
		tab = currentTab(tabId);
		expect(tab.generation.currentGeneration).toMatchObject({
			status: 'completed',
			file_type: 'video',
			current_video: '/api/media/generations/gen-b/0.mp4'
		});
		expect(tab.activeGenerationId).toBeNull();
		expect(tab.generation.queue).toEqual([]);
	});

	it('cancelling the newest (current) submission hands the display back to the older run that kept running', () => {
		const tabId = defaultTabId();
		seedNewestSubmissionOverOlderRunning(tabId);

		dispatchGenerationMessage({ type: 'generation_cancelled', generation_id: 'gen-b' } as any, { unsubscribe: vi.fn() });

		const tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBe('gen-a');
		expect(tab.generation.isGenerating).toBe(true);
		expect(tab.generation.currentGeneration).toEqual({ id: 'gen-a', generation_id: 'gen-a', status: 'running' });
		expect(tab.generation.queue).toEqual([{ generation_id: 'gen-a', queue_position: null, status: 'running' }]);
	});

	it('cancelling the only submission with nothing else queued clears the display (else null)', () => {
		const tabId = defaultTabId();
		const tab0 = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		tabsStore.updateTab(tabId, {
			activeGenerationId: 'gen-a',
			generation: {
				...tab0.generation,
				isGenerating: true,
				currentGeneration: { generation_id: 'gen-a', id: 'gen-a', status: 'running' },
				queue: [{ generation_id: 'gen-a', queue_position: null, status: 'running' }]
			}
		});

		dispatchGenerationMessage({ type: 'generation_cancelled', generation_id: 'gen-a' } as any, { unsubscribe: vi.fn() });

		const tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBeNull();
		expect(tab.generation.isGenerating).toBe(false);
		expect(tab.generation.currentGeneration).toBeNull();
		expect(tab.generation.queue).toEqual([]);
	});

	it('A (adopted after B is cancelled) restores its own cached video without a fresh gallery_update', () => {
		const tabId = defaultTabId();
		seedNewestSubmissionOverOlderRunning(tabId);

		// A produces output while merely backgrounded (not owner yet) -- cached,
		// not written to the tab's shared display.
		dispatchGenerationMessage(
			{
				type: 'gallery_update',
				generation_id: 'gen-a',
				videos: [{ path: '/api/media/generations/gen-a/0.mp4' }],
				video_urls_list: [{ path: '/api/media/generations/gen-a/0.mp4' }]
			} as any,
			{ unsubscribe: vi.fn() }
		);
		let tab = currentTab(tabId);
		expect(tab.generation.batchVideos).toEqual([]);

		// B is cancelled -- A (still running) is adopted and immediately shows
		// what it already produced, not a blank slate.
		dispatchGenerationMessage({ type: 'generation_cancelled', generation_id: 'gen-b' } as any, { unsubscribe: vi.fn() });
		tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBe('gen-a');
		expect(tab.generation.batchVideos).toHaveLength(1);
		expect(tab.generation.batchVideos[0].originalUrl).toBe('/api/media/generations/gen-a/0.mp4');
		expect(tab.generation.currentGeneration).toMatchObject({
			status: 'running',
			file_type: 'video',
			current_video: '/api/media/generations/gen-a/0.mp4'
		});

		// A completes without ever sending another gallery_update -- its batch
		// arrays must still reflect its own (already-known) output, not [].
		dispatchGenerationMessage({ type: 'generation_complete', data: { id: 'gen-a' } } as any, { unsubscribe: vi.fn() });
		tab = currentTab(tabId);
		expect(tab.generation.currentGeneration).toMatchObject({
			status: 'completed',
			file_type: 'video',
			current_video: '/api/media/generations/gen-a/0.mp4'
		});
		expect(tab.generation.batchVideos).toHaveLength(1);
		expect(tab.generation.batchVideos[0].originalUrl).toBe('/api/media/generations/gen-a/0.mp4');
		expect(tab.generation.workbenchTotal).toBe(1);
	});

	it('adoption and completion also restore a cached image (non-video control)', () => {
		const tabId = defaultTabId();
		seedNewestSubmissionOverOlderRunning(tabId);

		dispatchGenerationMessage(
			{
				type: 'gallery_update',
				generation_id: 'gen-a',
				images: ['base64-a'],
				image_urls_list: [{ original: '/api/media/generations/gen-a/0.png' }]
			} as any,
			{ unsubscribe: vi.fn() }
		);
		dispatchGenerationMessage({ type: 'generation_cancelled', generation_id: 'gen-b' } as any, { unsubscribe: vi.fn() });

		let tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBe('gen-a');
		expect(tab.generation.batchImages).toHaveLength(1);
		expect(tab.generation.batchImages[0].originalUrl).toBe('/api/media/generations/gen-a/0.png');
		expect(tab.generation.currentGeneration).toMatchObject({
			status: 'running',
			file_type: 'image',
			current_image: 'data:image/png;base64,base64-a'
		});

		dispatchGenerationMessage({ type: 'generation_complete', data: { id: 'gen-a' } } as any, { unsubscribe: vi.fn() });
		tab = currentTab(tabId);
		expect(tab.generation.batchImages).toHaveLength(1);
		expect(tab.generation.currentGeneration).toMatchObject({
			status: 'completed',
			file_type: 'image',
			current_image: 'data:image/png;base64,base64-a'
		});
	});
});
