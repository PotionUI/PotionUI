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
import { playGenerationCompleteSound } from '$lib/utils/generationSounds';
import {
	setGenerationOutputs,
	peekGenerationOutputs,
	isGenerationOutputsRetired,
	resetGenerationOutputsRetirementForTests
} from './generationOutputs';

// Importing '$lib/stores/generation' pulls in '$lib/generation/messages' as a
// side effect, which registers the generation_complete handler under test.

function defaultTabId(): string {
	return get(tabsStore).tabs[0].id;
}

function setCurrentGeneration(tabId: string, currentGeneration: any) {
	const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
	tabsStore.updateTab(tabId, { generation: { ...tab.generation, currentGeneration } });
}

describe('generation_complete message handler — sound gating', () => {
	beforeEach(() => {
		tabsStore.reset();
		vi.mocked(playGenerationCompleteSound).mockClear();
	});

	it('plays the complete sound when the owning tab has soundOnComplete enabled', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-1' });
		tabsStore.updateTab(tabId, { soundOnComplete: true });

		dispatchGenerationMessage({ type: 'generation_complete', data: { id: 'gen-1' } } as any, {
			unsubscribe: vi.fn()
		});

		expect(playGenerationCompleteSound).toHaveBeenCalledTimes(1);
	});

	it('does not play a sound when the owning tab has soundOnComplete disabled', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-2' });
		tabsStore.updateTab(tabId, { soundOnComplete: false });

		dispatchGenerationMessage({ type: 'generation_complete', data: { id: 'gen-2' } } as any, {
			unsubscribe: vi.fn()
		});

		expect(playGenerationCompleteSound).not.toHaveBeenCalled();
	});

	it('retires the generationOutputs cache -- a stray gallery_update for the same id afterwards cannot recreate it', () => {
		resetGenerationOutputsRetirementForTests();
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-3' });
		setGenerationOutputs('gen-3', {
			images: [],
			videos: [{ url: '/v.mp4', originalUrl: '/v.mp4' } as any],
			audios: [],
			meshes: []
		});
		const unsubscribe = vi.fn();

		dispatchGenerationMessage({ type: 'generation_complete', data: { id: 'gen-3' } } as any, { unsubscribe });

		expect(unsubscribe).toHaveBeenCalledWith('gen-3');
		expect(isGenerationOutputsRetired('gen-3')).toBe(true);

		dispatchGenerationMessage(
			{
				type: 'gallery_update',
				generation_id: 'gen-3',
				videos: [{ path: '/late.mp4' }],
				video_urls_list: [{ path: '/late.mp4' }]
			} as any,
			{ unsubscribe: vi.fn() }
		);
		expect(peekGenerationOutputs('gen-3')).toEqual({ images: [], videos: [], audios: [], meshes: [] });
	});
});
