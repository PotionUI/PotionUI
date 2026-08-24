import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { dispatchGenerationMessage } from '$lib/stores/generation';
import { tabsStore } from '$lib/stores/tabs';

function seedGeneration() {
	const tab = get(tabsStore).tabs[0];
	tabsStore.updateTab(tab.id, {
		generation: {
			...tab.generation,
			isGenerating: true,
			currentGeneration: {
				id: 'gen-video',
				generation_id: 'gen-video',
				status: 'running',
				file_type: 'image',
				current_image: 'data:image/png;base64,last-preview-frame'
			}
		}
	});
	return tab.id;
}

describe('workbench media transitions', () => {
	beforeEach(() => tabsStore.reset());

	it('prefers an authoritative video path over an accompanying preview image', () => {
		const tabId = seedGeneration();
		dispatchGenerationMessage(
			{
				type: 'workbench_update',
				generation_id: 'gen-video',
				file_type: 'video',
				path: '/api/media/generations/gen-video/final.mp4',
				image: 'last-preview-frame',
				fps: 24
			} as any,
			{ unsubscribe: vi.fn() }
		);

		const generation = get(tabsStore).tabs.find((tab) => tab.id === tabId)!.generation;
		expect(generation.currentGeneration).toMatchObject({
			file_type: 'video',
			current_video: '/api/media/generations/gen-video/final.mp4',
			current_image: null
		});
	});

	it('stores gallery videos and completes into the video player for video-only output', () => {
		const tabId = seedGeneration();
		const unsubscribe = vi.fn();
		dispatchGenerationMessage(
			{
				type: 'gallery_update',
				generation_id: 'gen-video',
				images: [],
				videos: [{ file_type: 'video', fps: 24, duration: 4 }],
				video_urls_list: [
					{ path: '/api/media/generations/gen-video/0.mp4', fps: 24, duration: 4 }
				],
				audios: []
			} as any,
			{ unsubscribe }
		);

		let generation = get(tabsStore).tabs.find((tab) => tab.id === tabId)!.generation;
		expect(generation.batchVideos).toHaveLength(1);
		expect(generation.workbenchTotal).toBe(1);

		dispatchGenerationMessage(
			{ type: 'generation_complete', data: { id: 'gen-video' } } as any,
			{ unsubscribe }
		);
		generation = get(tabsStore).tabs.find((tab) => tab.id === tabId)!.generation;
		expect(generation.currentGeneration).toMatchObject({
			status: 'completed',
			file_type: 'video',
			current_video: '/api/media/generations/gen-video/0.mp4',
			current_image: null
		});
		expect(generation.isGenerating).toBe(false);
	});

	it('completes onto the derived (enhanced) image while keeping stored order', () => {
		const tabId = seedGeneration();
		const unsubscribe = vi.fn();
		// One gallery message carrying base (index 0) + derived enhance (index 1),
		// the persisted file-index order — never reordered.
		dispatchGenerationMessage(
			{
				type: 'gallery_update',
				generation_id: 'gen-video',
				images: ['base64-base', 'base64-enhanced'],
				image_urls_list: [
					{ original: '/api/media/generations/gen-video/0.png', derived: false },
					{ original: '/api/media/generations/gen-video/1.png', derived: true }
				]
			} as any,
			{ unsubscribe }
		);

		let generation = get(tabsStore).tabs.find((tab) => tab.id === tabId)!.generation;
		expect(generation.batchImages.map((i: any) => i.originalUrl)).toEqual([
			'/api/media/generations/gen-video/0.png',
			'/api/media/generations/gen-video/1.png'
		]);
		expect(generation.batchImages.map((i: any) => i.derived)).toEqual([false, true]);

		dispatchGenerationMessage(
			{ type: 'generation_complete', data: { id: 'gen-video' } } as any,
			{ unsubscribe }
		);
		generation = get(tabsStore).tabs.find((tab) => tab.id === tabId)!.generation;
		expect(generation.workbenchIndex).toBe(1);
		expect(generation.currentGeneration).toMatchObject({
			status: 'completed',
			file_type: 'image',
			current_video: null
		});
		expect(generation.currentGeneration?.current_image).toContain('base64-enhanced');
	});
});
