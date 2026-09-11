// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// previewGeneration.ts outlives the phrasebook panel's mount/unmount (see the
// module header) - its poll must not keep hitting the API once the tab is
// backgrounded, and must pick back up without waiting for a fresh tick once
// it's visible again.

// A minimal hand-rolled store (not svelte/store's `writable`) so the mock
// factory below has no dependency on an import - vi.mock/vi.hoisted are
// hoisted above every import statement, so `writable` would still be
// undefined at the point this factory runs.
type PhrasebookState = { categoryValues: Record<string, { id: string; preview_file_id?: string }[]> };

const { loadCategoryValues, phrasebookState } = vi.hoisted(() => {
	let state: PhrasebookState = { categoryValues: {} };
	const listeners = new Set<(v: PhrasebookState) => void>();
	return {
		loadCategoryValues: vi.fn().mockResolvedValue(undefined),
		phrasebookState: {
			subscribe(fn: (v: PhrasebookState) => void) {
				listeners.add(fn);
				fn(state);
				return () => listeners.delete(fn);
			},
			set(next: PhrasebookState) {
				state = next;
				listeners.forEach((fn) => fn(state));
			}
		}
	};
});

vi.mock('$lib/services/api/index', () => ({
	api: {
		listPresets: vi.fn(),
		getSessionsForPreset: vi.fn(),
		getPresetModes: vi.fn(),
		getToken: vi.fn(() => null),
		setOnAuthExpired: vi.fn(),
		generatePreviews: vi.fn().mockResolvedValue({
			success: true,
			data: { started: 1, generations: [{ generation_id: 'gen-1' }] }
		})
	}
}));

vi.mock('$lib/services/websocket', () => ({
	createGenerationSocket: vi.fn(() => ({
		connect: vi.fn(),
		disconnect: vi.fn(),
		subscribe: vi.fn(),
		unsubscribe: vi.fn()
	})),
	WebSocketService: class {}
}));

vi.mock('$lib/stores/phrasebook', () => ({
	phrasebookStore: {
		subscribe: phrasebookState.subscribe,
		loadCategoryValues
	}
}));

import { previewGenerationStore } from './previewGeneration';

function setHidden(hidden: boolean) {
	Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
}

async function startPoll() {
	previewGenerationStore.setSelectedSessionId('session-1');
	previewGenerationStore.setSelectedMode('mode-1');
	await previewGenerationStore.handleGeneratePreviews('cat-1', new Set(['v1']));
}

describe('previewGenerationStore polling visibility', () => {
	beforeEach(() => {
		vi.useFakeTimers();
		loadCategoryValues.mockClear();
		phrasebookState.set({ categoryValues: {} });
		previewGenerationStore.reset();
		previewGenerationStore.connect();
		setHidden(false);
	});

	afterEach(() => {
		previewGenerationStore.disconnect();
		vi.useRealTimers();
	});

	it('does not poll while the document is hidden', async () => {
		await startPoll();
		loadCategoryValues.mockClear();

		setHidden(true);
		await vi.advanceTimersByTimeAsync(3000);

		expect(loadCategoryValues).not.toHaveBeenCalled();
	});

	it('does not count a hidden tick against the poll cap', async () => {
		await startPoll();

		setHidden(true);
		// Far more than maxPolls (60) worth of ticks while hidden.
		await vi.advanceTimersByTimeAsync(3000 * 200);

		expect(loadCategoryValues).not.toHaveBeenCalled();

		setHidden(false);
		document.dispatchEvent(new Event('visibilitychange'));
		await vi.advanceTimersByTimeAsync(0);

		// Still polling (would have hit "may still be in progress" if the
		// hidden ticks had counted against maxPolls).
		expect(loadCategoryValues).toHaveBeenCalledTimes(1);
	});

	it('resumes immediately on visibilitychange rather than waiting for the next tick', async () => {
		await startPoll();

		setHidden(true);
		await vi.advanceTimersByTimeAsync(3000);
		expect(loadCategoryValues).not.toHaveBeenCalled();

		setHidden(false);
		document.dispatchEvent(new Event('visibilitychange'));
		await vi.advanceTimersByTimeAsync(0);

		expect(loadCategoryValues).toHaveBeenCalledTimes(1);
	});

	it('stops ticking once polling is stopped', async () => {
		await startPoll();
		loadCategoryValues.mockClear();

		previewGenerationStore.disconnect();
		await vi.advanceTimersByTimeAsync(30000);

		expect(loadCategoryValues).not.toHaveBeenCalled();
	});
});
