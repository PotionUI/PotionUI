import { generationTabs } from '$lib/types/tabs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { dispatchGenerationMessage } from '$lib/stores/generation';
import { tabsStore } from '$lib/stores/tabs';
import { resetGenerationOutputsRetirementForTests } from './generationOutputs';
import { batchPromptFields } from '$lib/generation/promptBatch';

function dispatch(imageUrls: Record<string, unknown>[]) {
	const tab = generationTabs(get(tabsStore).tabs)[0];
	tabsStore.updateTab(tab.id, {
		generation: {
			...tab.generation,
			isGenerating: true,
			currentGeneration: { id: 'gen-batch', generation_id: 'gen-batch', status: 'running', file_type: 'image' }
		}
	});
	dispatchGenerationMessage(
		{
			type: 'gallery_update',
			generation_id: 'gen-batch',
			images: imageUrls.map(() => 'AAAA'),
			image_urls_list: imageUrls,
			videos: [],
			audios: []
		} as any,
		{ unsubscribe: vi.fn() }
	);
	return generationTabs(get(tabsStore).tabs).find((t) => t.id === tab.id)!.generation.batchImages;
}

describe('gallery_update during a prompt batch', () => {
	beforeEach(() => {
		tabsStore.reset();
		resetGenerationOutputsRetirementForTests();
	});

	it('keeps the prompt index and text and labels each tile prompt.image', () => {
		const images = dispatch([
			{ path: '/api/media/generations/gen-batch/0.png', prompt_index: 0, prompt: 'lighthouse' },
			{ path: '/api/media/generations/gen-batch/1.png', prompt_index: 0, prompt: 'lighthouse' },
			{ path: '/api/media/generations/gen-batch/2.png', prompt_index: 1, prompt: 'red fox' }
		]);

		expect(images.map((image) => [image.promptIndex, image.promptImage, image.label])).toEqual([
			[0, 1, '1.1'],
			[0, 2, '1.2'],
			[1, 1, '2.1']
		]);
		expect(images[2].promptText).toBe('red fox');
	});

	it('leaves a non-batch generation without prompt fields or labels', () => {
		const images = dispatch([{ path: '/api/media/generations/gen-batch/0.png' }]);

		expect(images[0].promptIndex).toBeUndefined();
		expect(images[0].promptText).toBeUndefined();
		expect(images[0].label).toBeNull();
	});

	it('does not replace a label the backend supplied', () => {
		const images = dispatch([{ path: '/api/media/generations/gen-batch/0.png', prompt_index: 2, label: 'Guide: Pose' }]);

		expect(images[0].label).toBe('Guide: Pose');
		expect(images[0].promptIndex).toBe(2);
	});
});

describe('batchPromptFields', () => {
	it('rejects a missing or malformed index', () => {
		expect(batchPromptFields([{}], 0)).toBeNull();
		expect(batchPromptFields([{ prompt_index: -1 }], 0)).toBeNull();
		expect(batchPromptFields([{ prompt_index: '1' }], 0)).toBeNull();
	});
});
