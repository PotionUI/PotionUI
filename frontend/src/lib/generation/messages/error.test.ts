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
import { isGenerationOutputsRetired, resetGenerationOutputsRetirementForTests } from './generationOutputs';

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

	it('retires the generationOutputs cache and unsubscribes on generation_error', () => {
		resetGenerationOutputsRetirementForTests();
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-4' });
		const unsubscribe = vi.fn();

		dispatchGenerationMessage({ type: 'generation_error', generation_id: 'gen-4', error: 'boom' } as any, {
			unsubscribe
		});

		expect(unsubscribe).toHaveBeenCalledWith('gen-4');
		expect(isGenerationOutputsRetired('gen-4')).toBe(true);
	});
});

describe('generation_error message handler — failure payloads', () => {
	beforeEach(() => {
		tabsStore.reset();
	});

	function failedGeneration(tabId: string) {
		return get(tabsStore).tabs.find((t) => t.id === tabId)!.generation.currentGeneration as any;
	}

	it('shows a regular user the safe message with the hint and error id', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-u' });

		dispatchGenerationMessage(
			{
				type: 'generation_error',
				generation_id: 'gen-u',
				error_code: 'cuda_oom',
				message: 'The GPU ran out of memory.',
				hint: '- Try a smaller resolution',
				error_id: 'gen-u'
			} as any,
			{ unsubscribe: vi.fn() }
		);

		const generation = failedGeneration(tabId);
		expect(generation.message).toBe('The GPU ran out of memory.');
		expect(generation.hint).toBe('- Try a smaller resolution');
		expect(generation.errorId).toBe('gen-u');
		expect(generation.errorDetail).toBeNull();
	});

	it('shows an admin the full detail when the payload carries it', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-a' });

		dispatchGenerationMessage(
			{
				type: 'generation_error',
				generation_id: 'gen-a',
				message: 'The GPU ran out of memory.',
				hint: '- Try a smaller resolution',
				error_id: 'gen-a',
				detail: 'Traceback (most recent call last): ...'
			} as any,
			{ unsubscribe: vi.fn() }
		);

		expect(failedGeneration(tabId).errorDetail).toContain('Traceback');
	});

	it('carries the hint and error id as discrete fields, independent of the detail blob', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-b' });

		dispatchGenerationMessage(
			{
				type: 'generation_error',
				generation_id: 'gen-b',
				message: 'The GPU ran out of memory.',
				hint: '- Lower the resolution one tier\n- Close other GPU applications',
				error_id: 'gen-b'
			} as any,
			{ unsubscribe: vi.fn() }
		);

		const generation = failedGeneration(tabId);
		expect(generation.hint).toBe('- Lower the resolution one tier\n- Close other GPU applications');
		expect(generation.errorId).toBe('gen-b');
		expect(generation.errorDetail).toBeNull();
	});
});

describe('generation_cancelled cancel notice', () => {
	const NOTICE = 'Stopped waiting. The provider may still finish this job and bill it.';

	beforeEach(() => {
		tabsStore.reset();
	});

	function noticeOf(tabId: string) {
		return get(tabsStore).tabs.find((t) => t.id === tabId)!.generation.cancelNotice;
	}

	it('keeps the notice the backend sends on the cancelled message', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-n1' });

		dispatchGenerationMessage(
			{ type: 'generation_cancelled', generation_id: 'gen-n1', data: { cancel_notice: NOTICE } } as any,
			{ unsubscribe: vi.fn() }
		);

		expect(noticeOf(tabId)).toBe(NOTICE);
		expect(get(tabsStore).tabs.find((t) => t.id === tabId)!.generation.isGenerating).toBe(false);
	});

	it('has no notice when the cancel was confirmed', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-n2' });

		dispatchGenerationMessage(
			{ type: 'generation_cancelled', generation_id: 'gen-n2', data: { cancel_notice: null } } as any,
			{ unsubscribe: vi.fn() }
		);

		expect(noticeOf(tabId)).toBeNull();
	});

	it('does not show a notice for an error, even one carrying the field', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-n3' });

		dispatchGenerationMessage(
			{ type: 'generation_error', generation_id: 'gen-n3', error: 'boom', data: { cancel_notice: NOTICE } } as any,
			{ unsubscribe: vi.fn() }
		);

		expect(noticeOf(tabId)).toBeNull();
	});

	it('does not touch another tab showing a different generation', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-shown' });

		dispatchGenerationMessage(
			{ type: 'generation_cancelled', generation_id: 'gen-other', data: { cancel_notice: NOTICE } } as any,
			{ unsubscribe: vi.fn() }
		);

		expect(noticeOf(tabId)).toBeFalsy();
	});
});

