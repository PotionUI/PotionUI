import { afterEach, describe, expect, it, vi } from 'vitest';
import { createContinuousGenerationScheduler } from './continuousGeneration';

describe('createContinuousGenerationScheduler', () => {
	afterEach(() => vi.useRealTimers());

	it('schedules one continuation when armed', () => {
		vi.useFakeTimers();
		const onContinue = vi.fn();
		const scheduler = createContinuousGenerationScheduler(onContinue);

		scheduler.arm();
		expect(scheduler.schedule()).toBe(true);
		vi.advanceTimersByTime(1_000);

		expect(onContinue).toHaveBeenCalledTimes(1);
	});

	it('disarm suppresses a pending continuation', () => {
		vi.useFakeTimers();
		const onContinue = vi.fn();
		const scheduler = createContinuousGenerationScheduler(onContinue);

		scheduler.arm();
		scheduler.schedule();
		scheduler.disarm();
		vi.advanceTimersByTime(1_000);

		expect(onContinue).not.toHaveBeenCalled();
	});

	it('does not create duplicate pending continuations', () => {
		vi.useFakeTimers();
		const onContinue = vi.fn();
		const scheduler = createContinuousGenerationScheduler(onContinue);

		scheduler.arm();
		expect(scheduler.schedule()).toBe(true);
		expect(scheduler.schedule()).toBe(false);
		vi.advanceTimersByTime(1_000);

		expect(onContinue).toHaveBeenCalledTimes(1);
	});

	it('dispose suppresses a pending continuation', () => {
		vi.useFakeTimers();
		const onContinue = vi.fn();
		const scheduler = createContinuousGenerationScheduler(onContinue);

		scheduler.arm();
		scheduler.schedule();
		scheduler.dispose();
		vi.advanceTimersByTime(1_000);

		expect(onContinue).not.toHaveBeenCalled();
	});
});
