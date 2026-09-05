import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { get } from 'svelte/store';

vi.mock('$lib/services/api', () => ({
	api: {
		previewGenerationMemory: vi.fn()
	}
}));

import { api } from '$lib/services/api';
import { createMemoryAdvisoryController, type MemoryAdvisoryState } from './memoryAdvisory';

const mockPreview = api.previewGenerationMemory as unknown as ReturnType<typeof vi.fn>;

function result(checkpointEstimateGb: number | null): any {
	return {
		success: true,
		data: {
			estimate: { checkpoint_estimate_gb: checkpointEstimateGb, weights_gb: 0, activation_gb: 0, margin: 1.1, basis: 'x' },
			coverage: { known: [], unknown: [], active_set_resolved: true, pinned_components_uncounted: true, uncertainty: [] },
			device: { kind: 'local', free_gb: 10, total_gb: 24, provenance: 'x' },
			budget: { configured_gb: 10, source: 'x', pipe_hints_gb: [] },
			backend: { id: 'b1', name: 'Local', engine: 'native', driver: 'native.local' }
		}
	};
}

function baseInput(overrides: Partial<{ presetId: string | null; formData: Record<string, unknown> }> = {}) {
	return {
		presetId: 'preset_1',
		mode: 'txt2img',
		formData: { steps: 20 },
		...overrides
	};
}

beforeEach(() => {
	vi.useFakeTimers();
	mockPreview.mockReset();
});

afterEach(() => {
	vi.useRealTimers();
});

describe('createMemoryAdvisoryController', () => {
	it('starts idle', () => {
		const controller = createMemoryAdvisoryController();
		expect(get(controller)).toEqual({ status: 'idle', result: null, error: null });
		controller.dispose();
	});

	it('debounces a burst of refresh calls into exactly one request', async () => {
		mockPreview.mockResolvedValue(result(5));
		const controller = createMemoryAdvisoryController();

		controller.refresh(baseInput({ formData: { steps: 1 } }));
		vi.advanceTimersByTime(100);
		controller.refresh(baseInput({ formData: { steps: 2 } }));
		vi.advanceTimersByTime(100);
		controller.refresh(baseInput({ formData: { steps: 3 } }));

		// Still within the debounce window of the LAST call - nothing sent yet.
		vi.advanceTimersByTime(399);
		expect(mockPreview).not.toHaveBeenCalled();

		vi.advanceTimersByTime(1);
		await vi.waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1));
		expect(mockPreview.mock.calls[0][0]).toMatchObject({ form_data: { steps: 3 } });

		controller.dispose();
	});

	it('resets to idle immediately when presetId becomes null, without waiting for the debounce', () => {
		const controller = createMemoryAdvisoryController();
		controller.refresh(baseInput());
		expect(get(controller).status).toBe('loading');

		controller.refresh(baseInput({ presetId: null }));
		expect(get(controller)).toEqual({ status: 'idle', result: null, error: null });

		vi.advanceTimersByTime(1000);
		expect(mockPreview).not.toHaveBeenCalled();

		controller.dispose();
	});

	// -- unchanged-input control -----------------------------------------------

	it('unchanged-input control: a duplicate refresh while a request is in flight does not disturb its eventual publish', async () => {
		mockPreview.mockResolvedValue(result(5));
		const controller = createMemoryAdvisoryController();

		controller.refresh(baseInput());
		vi.advanceTimersByTime(400);
		await vi.waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1));

		await vi.waitFor(() => expect(get(controller).status).toBe('ready'));

		// Identical input again, now that the request has already settled -
		// must be a no-op: no re-issue, no disturbance of the published result.
		controller.refresh(baseInput());
		expect(get(controller)).toMatchObject({ result: { estimate: { checkpoint_estimate_gb: 5 } } });
		expect(mockPreview).toHaveBeenCalledTimes(1);

		controller.dispose();
	});

	// -- current-response control ------------------------------------------------

	it('current-response control: a single request publishes normally with nothing else in flight', async () => {
		mockPreview.mockResolvedValue(result(7));
		const controller = createMemoryAdvisoryController();

		controller.refresh(baseInput());
		vi.advanceTimersByTime(400);
		await vi.waitFor(() => expect(get(controller).status).toBe('ready'));
		expect(get(controller)).toMatchObject({ result: { estimate: { checkpoint_estimate_gb: 7 } } });

		controller.dispose();
	});

	// -- ownership invalidation: the three interleavings a plain increasing
	// sequence number gets wrong (see memoryAdvisory.ts's module docstring) --

	it('drops a stale response for A once B is refresh()-ed, even while B is still debouncing (not yet issued)', async () => {
		let resolveA: (v: any) => void = () => {};
		mockPreview.mockImplementationOnce(() => new Promise((resolve) => (resolveA = resolve)));
		const controller = createMemoryAdvisoryController();

		controller.refresh(baseInput({ formData: { steps: 1 } })); // A
		vi.advanceTimersByTime(400);
		await vi.waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1));

		controller.refresh(baseInput({ formData: { steps: 2 } })); // B - ownership transfers NOW
		expect(get(controller).status).toBe('loading');
		// B is still within its own debounce window - never issued yet.
		expect(mockPreview).toHaveBeenCalledTimes(1);

		resolveA(result(1));
		await Promise.resolve();
		await Promise.resolve();

		// Must still be B's loading placeholder - never A's stale number.
		expect(get(controller)).toMatchObject({ status: 'loading', result: null });

		controller.dispose();
	});

	it('drops a stale response for A once B is issued and in flight, even before B itself resolves', async () => {
		let resolveA: (v: any) => void = () => {};
		let resolveB: (v: any) => void = () => {};
		mockPreview
			.mockImplementationOnce(() => new Promise((resolve) => (resolveA = resolve)))
			.mockImplementationOnce(() => new Promise((resolve) => (resolveB = resolve)));

		const controller = createMemoryAdvisoryController();

		controller.refresh(baseInput({ formData: { steps: 1 } })); // A
		vi.advanceTimersByTime(400);
		controller.refresh(baseInput({ formData: { steps: 2 } })); // B
		vi.advanceTimersByTime(400);
		await vi.waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(2));

		resolveA(result(1));
		await Promise.resolve();
		await Promise.resolve();
		expect(get(controller)).toMatchObject({ status: 'loading', result: null }); // still waiting on B

		resolveB(result(9));
		await Promise.resolve();
		await Promise.resolve();
		expect(get(controller)).toMatchObject({ status: 'ready', result: { estimate: { checkpoint_estimate_gb: 9 } } });

		controller.dispose();
	});

	it('never resurrects a stale response once the preset is cleared', async () => {
		let resolveA: (v: any) => void = () => {};
		mockPreview.mockImplementationOnce(() => new Promise((resolve) => (resolveA = resolve)));
		const controller = createMemoryAdvisoryController();

		controller.refresh(baseInput());
		vi.advanceTimersByTime(400);
		await vi.waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1));

		controller.refresh(baseInput({ presetId: null }));
		expect(get(controller)).toEqual({ status: 'idle', result: null, error: null });

		resolveA(result(5));
		await Promise.resolve();
		await Promise.resolve();
		expect(get(controller)).toEqual({ status: 'idle', result: null, error: null });

		controller.dispose();
	});

	it('surfaces a rejected request as an error state', async () => {
		mockPreview.mockRejectedValue(new Error('network down'));
		const controller = createMemoryAdvisoryController();

		controller.refresh(baseInput());
		vi.advanceTimersByTime(400);

		await vi.waitFor(() => {
			const state = get(controller) as MemoryAdvisoryState;
			expect(state.status).toBe('error');
		});
		expect(get(controller).error).toBe('network down');

		controller.dispose();
	});

	it('surfaces a well-formed but unsuccessful API response as an error state', async () => {
		mockPreview.mockResolvedValue({ success: false, error: 'validation_error', message: 'bad form' });
		const controller = createMemoryAdvisoryController();

		controller.refresh(baseInput());
		vi.advanceTimersByTime(400);

		await vi.waitFor(() => expect(get(controller).status).toBe('error'));
		expect(get(controller).error).toBe('bad form');

		controller.dispose();
	});

	it('never issues a request after dispose', () => {
		const controller = createMemoryAdvisoryController();
		controller.refresh(baseInput());
		controller.dispose();

		vi.advanceTimersByTime(1000);
		expect(mockPreview).not.toHaveBeenCalled();
	});
});
