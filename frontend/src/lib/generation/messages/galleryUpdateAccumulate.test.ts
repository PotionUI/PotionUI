import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { dispatchGenerationMessage } from '$lib/stores/generation';
import { tabsStore } from '$lib/stores/tabs';
import { resetGenerationOutputsRetirementForTests } from './generationOutputs';

const ID = 'gen-acc';

function start() {
	const tab = get(tabsStore).tabs[0];
	tabsStore.updateTab(tab.id, {
		generation: {
			...tab.generation,
			isGenerating: true,
			currentGeneration: { id: ID, generation_id: ID, status: 'running', file_type: 'image' }
		}
	});
	return tab.id;
}

function send(entries: Record<string, unknown>[], images: string[] = entries.map(() => 'AAAA')) {
	dispatchGenerationMessage(
		{
			type: 'gallery_update',
			generation_id: ID,
			images,
			image_urls_list: entries,
			videos: [],
			audios: []
		} as any,
		{ unsubscribe: vi.fn() }
	);
}

function generation(tabId: string) {
	return get(tabsStore).tabs.find((t) => t.id === tabId)!.generation;
}

describe('gallery_update accumulation', () => {
	beforeEach(() => {
		tabsStore.reset();
		resetGenerationOutputsRetirementForTests();
	});

	it('keeps the result when a labelled guide arrives in a second update', () => {
		const tabId = start();
		send([{ path: `/api/media/generations/${ID}/0.png` }]);
		send([{ path: `/api/media/generations/${ID}/1.png`, label: 'Guide: Pose' }]);

		const gen = generation(tabId);
		expect(gen.batchImages).toHaveLength(2);
		expect(gen.batchImages[0].originalUrl).toBe(`/api/media/generations/${ID}/0.png`);
		expect(gen.batchImages[1].label).toBe('Guide: Pose');
		expect(gen.workbenchTotal).toBe(2);
	});

	it('still shows both files after the generation completes', () => {
		const tabId = start();
		send([{ path: `/api/media/generations/${ID}/0.png` }]);
		send([{ path: `/api/media/generations/${ID}/1.png`, label: 'Guide: Pose' }]);
		dispatchGenerationMessage(
			{ type: 'generation_complete', generation_id: ID, data: { status: 'completed' } } as any,
			{ unsubscribe: vi.fn() }
		);

		const gen = generation(tabId);
		expect(gen.batchImages.map((i) => i.label ?? null)).toEqual([null, 'Guide: Pose']);
		expect(gen.workbenchTotal).toBe(2);
	});

	it('does not duplicate an image that is sent again', () => {
		const tabId = start();
		send([{ path: `/api/media/generations/${ID}/0.png` }]);
		send([{ path: `/api/media/generations/${ID}/0.png` }]);

		expect(generation(tabId).batchImages).toHaveLength(1);
	});

	it('replaces unsaved previews with the next update', () => {
		const tabId = start();
		send([], ['AAAA', 'BBBB']);
		send([], ['CCCC']);

		expect(generation(tabId).batchImages).toHaveLength(1);
	});
});
