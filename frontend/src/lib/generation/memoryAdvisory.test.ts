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

function result(lowerBoundGb: number | null): any {
	return {
		success: true,
		data: {
			estimate: { lower_bound_gb: lowerBoundGb, weights_gb: 0, activation_gb: 0, margin: 1.1, basis: 'x' },
			coverage: { known: [], unknown: [], active_set_resolved: true, pinned_components_uncounted: true, uncertainty: [] },
			device: { kind: 'local', free_gb: 10, total_gb: 24, provenance: 'x' },
			budget: { configured_gb: 10, source: 'x' },
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

	it('is a no-op for an input identical to the last one actually issued', async () => {
		mockPreview.mockResolvedValue(result(5));
		const controller = createMemoryAdvisoryController();

		controller.refresh(baseInput());
		vi.advanceTimersByTime(400);
		await vi.waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1));

		// Same input again - must not restart loading or re-issue.
		controller.refresh(baseInput());
		expect(get(controller).status).toBe('ready');
		vi.advanceTimersByTime(400);
		expect(mockPreview).toHaveBeenCalledTimes(1);

		controller.dispose();
	});

	it('discards a stale response for an input issued before the current one (delayed A, then B)', async () => {
		let resolveA: (v: any) => void = () => {};
		let resolveB: (v: any) => void = () => {};
		mockPreview
			.mockImplementationOnce(() => new Promise((resolve) => (resolveA = resolve)))
			.mockImplementationOnce(() => new Promise((resolve) => (resolveB = resolve)));

		const controller = createMemoryAdvisoryController();

		controller.refresh(baseInput({ formData: { steps: 1 } })); // A
		vi.advanceTimersByTime(400);

		controller.refresh(baseInput({ formData: { steps: 2 } })); // B - past debounce of A, distinct input
		vi.advanceTimersByTime(400);

		await vi.waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(2));

		// B resolves first...
		resolveB(result(9));
		await Promise.resolve();
		await Promise.resolve();
		expect(get(controller)).toMatchObject({ status: 'ready', result: { estimate: { lower_bound_gb: 9 } } });

		// ...then A's late response arrives - must NOT overwrite B's result.
		resolveA(result(1));
		await Promise.resolve();
		await Promise.resolve();
		expect(get(controller)).toMatchObject({ status: 'ready', result: { estimate: { lower_bound_gb: 9 } } });

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
