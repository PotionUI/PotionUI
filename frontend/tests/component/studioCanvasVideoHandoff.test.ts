// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { GenerationState, Tab } from '$lib/types/tabs';

const { default: StudioCanvas } = await import('../../src/routes/generate/components/studio/StudioCanvas.svelte');
const { createClassComponent } = await import('svelte/legacy');

const PREVIEW = 'data:image/png;base64,last-preview';
const VIDEO_URL = '/api/media/generations/gen-1/0.mp4';

function generation(overrides: Partial<GenerationState> = {}): GenerationState {
	return {
		isGenerating: false,
		currentGeneration: null,
		currentProgress: null,
		pipeTimers: {},
		startedAt: null,
		totalTime: null,
		lastDurationMs: null,
		batchImages: [],
		batchVideos: [],
		batchAudios: [],
		artifacts: [],
		workbenchIndex: 0,
		workbenchTotal: 0,
		queue: [],
		submittedPromptTemplate: null,
		...overrides
	} as GenerationState;
}

const tabWith = (gen: GenerationState) => ({ id: 'deck-tab', name: 'Tab', generation: gen }) as unknown as Tab;

let target: HTMLElement | undefined;
let component: { $destroy: () => void; $set: (props: Record<string, unknown>) => void } | undefined;

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target?.remove();
	target = undefined;
});

describe('studio canvas video result landing', () => {
	it('keeps the last preview until the visible player has a frame', () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		component = createClassComponent({
			component: StudioCanvas as never,
			target,
			props: {
				tab: tabWith(
					generation({
						isGenerating: true,
						currentGeneration: { id: 'gen-1', generation_id: 'gen-1', status: 'running', file_type: 'video', current_image: PREVIEW } as never
					})
				)
			}
		}) as unknown as typeof component;
		flushSync();
		const overlay = target.querySelector('[data-preview-hold]');
		expect(overlay).not.toBeNull();

		component!.$set({
			tab: tabWith(
				generation({
					isGenerating: false,
					currentGeneration: { id: 'gen-1', generation_id: 'gen-1', status: 'completed', file_type: 'video', current_image: null } as never,
					batchVideos: [{ url: VIDEO_URL, originalUrl: VIDEO_URL, file_type: 'video' }] as never,
					workbenchTotal: 1
				})
			)
		});
		flushSync();

		const player = target.querySelector('video')!;
		expect(target.querySelector('[data-preview-hold]')).toBe(overlay);
		expect(player).not.toBeNull();
		expect(player.className).not.toContain('opacity-0');

		player.dispatchEvent(new Event('loadeddata'));
		flushSync();

		expect(target.querySelector('[data-preview-hold]')).toBeNull();
		expect(target.querySelector('video')).toBe(player);
	});

	function mountGenerating() {
		target = document.createElement('div');
		document.body.appendChild(target);
		component = createClassComponent({
			component: StudioCanvas as never,
			target,
			props: {
				tab: tabWith(
					generation({
						isGenerating: true,
						currentGeneration: { id: 'gen-1', generation_id: 'gen-1', status: 'running', file_type: 'video', current_image: PREVIEW } as never
					})
				)
			}
		}) as unknown as typeof component;
		flushSync();
		return target;
	}

	it.each([
		['fails', { isGenerating: false, currentGeneration: { id: 'gen-1', generation_id: 'gen-1', status: 'failed', file_type: 'video', current_image: PREVIEW } }],
		['is cancelled', { isGenerating: false, currentGeneration: null }],
		['is replaced by a new generation', { isGenerating: true, currentGeneration: { id: 'gen-2', generation_id: 'gen-2', status: 'pending', file_type: 'video' } }]
	])('clears the hold when the generation %s', (_name, patch) => {
		const root = mountGenerating();
		expect(root.querySelector('[data-preview-hold]')).not.toBeNull();

		component!.$set({ tab: tabWith(generation(patch as never)) });
		flushSync();

		expect(root.querySelector('[data-preview-hold]')).toBeNull();
	});
});
