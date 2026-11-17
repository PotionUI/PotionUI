import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { dispatchGenerationMessage } from '$lib/stores/generation';
import { generationTabs } from '$lib/types/tabs';
import { resetPreviewReplay, resetPreviewReplayForTests, shouldApplyPreview } from './replayGuard';

beforeEach(() => {
	tabsStore.reset();
	resetPreviewReplayForTests();
});

describe('shouldApplyPreview', () => {
	it('applies the first replay, then ignores any further replay of the same preview', () => {
		expect(shouldApplyPreview('g1', 'frame', { replayed: true })).toBe(true);
		expect(shouldApplyPreview('g1', 'frame', { replayed: true })).toBe(false);
	});

	it('ignores a replay that arrives after a live preview, which is newer', () => {
		expect(shouldApplyPreview('g1', 'frame', {})).toBe(true);
		expect(shouldApplyPreview('g1', 'frame', { replayed: true })).toBe(false);
	});

	it('always applies live previews, even after a replay', () => {
		shouldApplyPreview('g1', 'frame', { replayed: true });
		expect(shouldApplyPreview('g1', 'frame', {})).toBe(true);
		expect(shouldApplyPreview('g1', 'frame', {})).toBe(true);
	});

	it('keeps pictures, clips and generations apart', () => {
		shouldApplyPreview('g1', 'frame', { replayed: true });
		expect(shouldApplyPreview('g1', 'clip', { replayed: true })).toBe(true);
		expect(shouldApplyPreview('g2', 'frame', { replayed: true })).toBe(true);
	});

	it('lets a generation opened again in a fresh tab replay once more', () => {
		shouldApplyPreview('g1', 'frame', { replayed: true });
		resetPreviewReplay('g1');
		expect(shouldApplyPreview('g1', 'frame', { replayed: true })).toBe(true);
	});

	it('is bounded', () => {
		for (let index = 0; index < 200; index++) shouldApplyPreview(`g${index}`, 'frame', { replayed: true });
		expect(shouldApplyPreview('g0', 'frame', { replayed: true })).toBe(true);
		expect(shouldApplyPreview('g199', 'frame', { replayed: true })).toBe(false);
	});
});

describe('the preview handlers', () => {
	function seed(generationId: string) {
		const tab = generationTabs(get(tabsStore).tabs)[0];
		tabsStore.updateTab(tab.id, {
			generation: {
				...tab.generation,
				currentGeneration: { id: generationId, generation_id: generationId, status: 'running' } as never
			}
		});
		return tab.id;
	}

	const shown = (tabId: string) => generationTabs(get(tabsStore).tabs).find((t) => t.id === tabId)!.generation.currentGeneration as any;
	const frame = (image: string, extra: Record<string, unknown> = {}) =>
		({ type: 'workbench_update', generation_id: 'g1', pipe_id: 1, image, ...extra }) as never;
	const clip = (step: number, extra: Record<string, unknown> = {}) =>
		({
			type: 'workbench_preview_clip',
			generation_id: 'g1',
			image: 'data:image/webp;base64,AAAA',
			frames: 25,
			columns: 5,
			frame_width: 384,
			frame_height: 216,
			fps: 8,
			step,
			total_steps: 30,
			source_indices: [],
			...extra
		}) as never;

	it('shows a replayed picture once and never lets it cover a newer live one', () => {
		const tabId = seed('g1');
		const deps = { unsubscribe: vi.fn() };
		dispatchGenerationMessage(frame('/api/preview/old.png', { replayed: true }), deps);
		expect(shown(tabId).current_image).toBe('/api/preview/old.png');
		dispatchGenerationMessage(frame('/api/preview/live.png'), deps);
		dispatchGenerationMessage(frame('/api/preview/old.png', { replayed: true }), deps);
		expect(shown(tabId).current_image).toBe('/api/preview/live.png');
	});

	it('shows a replayed clip once and never lets it cover a newer live one', () => {
		const tabId = seed('g1');
		const deps = { unsubscribe: vi.fn() };
		dispatchGenerationMessage(clip(3, { replayed: true }), deps);
		expect(shown(tabId).preview_clip.step).toBe(3);
		dispatchGenerationMessage(clip(9), deps);
		dispatchGenerationMessage(clip(3, { replayed: true }), deps);
		expect(shown(tabId).preview_clip.step).toBe(9);
	});

	it('does not apply the same replay twice', () => {
		const tabId = seed('g1');
		const deps = { unsubscribe: vi.fn() };
		dispatchGenerationMessage(frame('/api/preview/old.png', { replayed: true }), deps);
		tabsStore.updateTab(tabId, {
			generation: { ...generationTabs(get(tabsStore).tabs)[0].generation, currentGeneration: { id: 'g1', generation_id: 'g1', status: 'running', current_image: '/api/preview/mine.png' } as never }
		});
		dispatchGenerationMessage(frame('/api/preview/old.png', { replayed: true }), deps);
		expect(shown(tabId).current_image).toBe('/api/preview/mine.png');
	});
});
