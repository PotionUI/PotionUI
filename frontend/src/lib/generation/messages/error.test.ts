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
import { playGenerationErrorSound } from '$lib/utils/generationSounds';

// Importing '$lib/stores/generation' pulls in '$lib/generation/messages' as a
// side effect, which registers the generation_error/generation_cancelled
// handler under test.

function defaultTabId(): string {
	return get(tabsStore).tabs[0].id;
}

function setCurrentGeneration(tabId: string, currentGeneration: any) {
	const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
	tabsStore.updateTab(tabId, { generation: { ...tab.generation, currentGeneration } });
}

describe('generation_error / generation_cancelled message handler — sound gating', () => {
	beforeEach(() => {
		tabsStore.reset();
		vi.mocked(playGenerationErrorSound).mockClear();
	});

	it('plays the error sound on generation_error when the owning tab has soundOnError enabled', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-1' });
		tabsStore.updateTab(tabId, { soundOnError: true });

		dispatchGenerationMessage({ type: 'generation_error', generation_id: 'gen-1', error: 'boom' } as any, {
			unsubscribe: vi.fn()
		});

		expect(playGenerationErrorSound).toHaveBeenCalledTimes(1);
	});

	it('does not play a sound on generation_error when the owning tab has soundOnError disabled', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-2' });
		tabsStore.updateTab(tabId, { soundOnError: false });

		dispatchGenerationMessage({ type: 'generation_error', generation_id: 'gen-2', error: 'boom' } as any, {
			unsubscribe: vi.fn()
		});

		expect(playGenerationErrorSound).not.toHaveBeenCalled();
	});

	it('never plays a sound for generation_cancelled, even when soundOnError is enabled', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-3' });
		tabsStore.updateTab(tabId, { soundOnError: true });

		dispatchGenerationMessage({ type: 'generation_cancelled', generation_id: 'gen-3' } as any, {
			unsubscribe: vi.fn()
		});

		expect(playGenerationErrorSound).not.toHaveBeenCalled();
	});
});
