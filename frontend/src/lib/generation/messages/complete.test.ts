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
});
