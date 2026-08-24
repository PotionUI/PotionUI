import { afterEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { createGenerationModeController } from './generationModeController';

describe('createGenerationModeController', () => {
	afterEach(() => vi.useRealTimers());

	it('continues automatically while in continuous mode', () => {
		vi.useFakeTimers();
		const onContinue = vi.fn();
		const controller = createGenerationModeController(onContinue);

		controller.setMode('forever');
		controller.handleGenerationComplete();
		vi.advanceTimersByTime(1_000);

		expect(onContinue).toHaveBeenCalledTimes(1);
	});

	it('stop after current suppresses the queued continuation without touching the running generation', () => {
		vi.useFakeTimers();
		const onContinue = vi.fn();
		const onCancel = vi.fn();
		const controller = createGenerationModeController(onContinue);

		controller.setMode('forever');
		// A generation is in flight; "stop after current" is clicked mid-run.
		controller.requestStopAfterCurrent();

		expect(get(controller.mode)).toBe('once');
		expect(get(controller.stopAfterCurrentRequested)).toBe(true);
		expect(onCancel).not.toHaveBeenCalled();

		// The in-flight generation finishes on its own — not aborted by the stop request.
		controller.handleGenerationComplete();
		vi.advanceTimersByTime(1_000);

		expect(onContinue).not.toHaveBeenCalled();
		expect(get(controller.stopAfterCurrentRequested)).toBe(false);
	});

	it('cancel disarms the scheduler so the aborted run does not queue a continuation', () => {
		vi.useFakeTimers();
		const onContinue = vi.fn();
		const controller = createGenerationModeController(onContinue);

		controller.setMode('forever');
		controller.cancel();

		expect(get(controller.mode)).toBe('once');

		controller.handleGenerationComplete();
		vi.advanceTimersByTime(1_000);

		expect(onContinue).not.toHaveBeenCalled();
	});

	it('starting a new generation clears a stale stop-after-current flag', () => {
		const controller = createGenerationModeController(vi.fn());

		controller.setMode('forever');
		controller.requestStopAfterCurrent();
		expect(get(controller.stopAfterCurrentRequested)).toBe(true);

		controller.handleGenerationStart();
		expect(get(controller.stopAfterCurrentRequested)).toBe(false);
	});

	it('dispose prevents a queued continuation from firing after unmount', () => {
		vi.useFakeTimers();
		const onContinue = vi.fn();
		const controller = createGenerationModeController(onContinue);

		controller.setMode('forever');
		controller.handleGenerationComplete();
		controller.dispose();
		vi.advanceTimersByTime(1_000);

		expect(onContinue).not.toHaveBeenCalled();
	});
});
