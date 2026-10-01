import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { dispatchGenerationMessage } from '$lib/stores/generation';
import { tabsStore } from '$lib/stores/tabs';
import { resetGenerationOutputsRetirementForTests } from './generationOutputs';

function dispatch(imageUrls: Record<string, unknown>[]) {
	const tab = get(tabsStore).tabs[0];
	tabsStore.updateTab(tab.id, {
		generation: {
			...tab.generation,
			isGenerating: true,
			currentGeneration: { id: 'gen-label', generation_id: 'gen-label', status: 'running', file_type: 'image' }
		}
	});
	dispatchGenerationMessage(
		{
			type: 'gallery_update',
			generation_id: 'gen-label',
			images: imageUrls.map(() => 'AAAA'),
			image_urls_list: imageUrls,
			videos: [],
			audios: []
		} as any,
		{ unsubscribe: vi.fn() }
	);
	return get(tabsStore).tabs.find((t) => t.id === tab.id)!.generation.batchImages;
}

describe('gallery_update output label', () => {
	beforeEach(() => {
		tabsStore.reset();
		resetGenerationOutputsRetirementForTests();
	});

	it('keeps the label of a labelled image and leaves the others without one', () => {
		const images = dispatch([
			{ path: '/api/media/generations/gen-label/0.png' },
			{ path: '/api/media/generations/gen-label/1.png', derived: true, label: 'Guide: Pose' }
		]);

		expect(images[0].label).toBeNull();
		expect(images[1].label).toBe('Guide: Pose');
	});

	it('gives an image without a metadata entry no label', () => {
		const tab = get(tabsStore).tabs[0];
		tabsStore.updateTab(tab.id, {
			generation: {
				...tab.generation,
				isGenerating: true,
				currentGeneration: { id: 'gen-label', generation_id: 'gen-label', status: 'running', file_type: 'image' }
			}
		});
		dispatchGenerationMessage(
			{
				type: 'gallery_update',
				generation_id: 'gen-label',
				images: ['AAAA'],
				image_urls_list: [],
				videos: [],
				audios: []
			} as any,
			{ unsubscribe: vi.fn() }
		);
		const images = get(tabsStore).tabs.find((t) => t.id === tab.id)!.generation.batchImages;

		expect(images).toHaveLength(1);
		expect(images[0].label).toBeNull();
	});
});
