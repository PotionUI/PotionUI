import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from './tabs';
import { dispatchGenerationMessage, findTabByGenerationId } from './generation';

// Tab ids are crypto.randomUUID() (not the literal 'tab-1' of older builds), so
// every test resolves the default tab's id from the store rather than assuming it.
function defaultTabId(): string {
	return get(tabsStore).tabs[0].id;
}

function setCurrentGeneration(tabId: string, currentGeneration: any) {
	const state = get(tabsStore);
	const tab = state.tabs.find((t) => t.id === tabId)!;
	tabsStore.updateTab(tabId, {
		generation: { ...tab.generation, currentGeneration }
	});
}

describe('findTabByGenerationId', () => {
	beforeEach(() => tabsStore.reset());

	it('returns null when generationId is undefined', () => {
		expect(findTabByGenerationId(undefined)).toBeNull();
	});

	it('returns null when no tab owns the generation', () => {
		expect(findTabByGenerationId('gen-does-not-exist')).toBeNull();
	});

	it('finds a tab by currentGeneration.generation_id', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-123' });
		expect(findTabByGenerationId('gen-123')).toBe(tabId);
	});

	it('finds a tab by currentGeneration.id (legacy shape)', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { id: 'gen-456' });
		expect(findTabByGenerationId('gen-456')).toBe(tabId);
	});

	it('finds a tab by a queued (non-current) generation in generation.queue', () => {
		const tabId = defaultTabId();
		const state = get(tabsStore);
		const tab = state.tabs.find((t) => t.id === tabId)!;
		tabsStore.updateTab(tabId, {
			generation: {
				...tab.generation,
				queue: [{ generation_id: 'gen-queued', queue_position: 2, status: 'pending' }]
			}
		});
		expect(findTabByGenerationId('gen-queued')).toBe(tabId);
	});
});

describe('dispatchGenerationMessage', () => {
	beforeEach(() => tabsStore.reset());

	it('extracts the generation id from the top level for a regular message', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-1' });

		dispatchGenerationMessage(
			{ type: 'generation_status', generation_id: 'gen-1', progress: 0.5 } as any,
			{ unsubscribe: vi.fn() }
		);

		const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		expect(tab.generation.currentProgress).toMatchObject({ type: 'generation_status', progress: 0.5 });
	});

	it('extracts the generation id from data.id for completion messages (the id-extraction quirk)', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-2' });
		const unsubscribe = vi.fn();

		dispatchGenerationMessage(
			{ type: 'generation_complete', data: { id: 'gen-2' } } as any,
			{ unsubscribe }
		);

		expect(unsubscribe).toHaveBeenCalledWith('gen-2');
		const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		expect(tab.generation.isGenerating).toBe(false);
	});

	it('falls back to data.generation_id, then top-level generation_id, for completion messages', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-3' });
		const unsubscribe = vi.fn();

		dispatchGenerationMessage(
			{ type: 'generation_error', data: { generation_id: 'gen-3', message: 'boom' } } as any,
			{ unsubscribe }
		);

		expect(unsubscribe).toHaveBeenCalledWith('gen-3');
		const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		expect(tab.generation.currentGeneration).toMatchObject({ status: 'failed', message: 'boom' });
	});

	it('reads a structured generation_error message (top-level error + detail), not the generic fallback', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-5' });
		const unsubscribe = vi.fn();

		dispatchGenerationMessage(
			{
				type: 'generation_error',
				generation_id: 'gen-5',
				error: 'Model failed to load',
				detail: 'Traceback (most recent call last): ...'
			} as any,
			{ unsubscribe }
		);

		expect(unsubscribe).toHaveBeenCalledWith('gen-5');
		const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		expect(tab.generation.currentGeneration).toMatchObject({
			status: 'failed',
			message: 'Model failed to load',
			errorDetail: 'Traceback (most recent call last): ...'
		});
	});

	it('does nothing (and does not throw) when no tab owns the generation id', () => {
		const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

		expect(() =>
			dispatchGenerationMessage(
				{ type: 'generation_status', generation_id: 'unknown-gen' } as any,
				{ unsubscribe: vi.fn() }
			)
		).not.toThrow();

		expect(errorSpy).toHaveBeenCalled();
		errorSpy.mockRestore();
	});

	it('logs unknown message types once and otherwise no-ops', () => {
		const tabId = defaultTabId();
		setCurrentGeneration(tabId, { generation_id: 'gen-4' });
		const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

		const message = { type: 'some_future_message_type', generation_id: 'gen-4' } as any;
		dispatchGenerationMessage(message, { unsubscribe: vi.fn() });
		dispatchGenerationMessage(message, { unsubscribe: vi.fn() });

		expect(warnSpy).toHaveBeenCalledTimes(1);
		warnSpy.mockRestore();
	});
});
