// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { whenIdle } from './idle';

afterEach(() => {
	vi.useRealTimers();
	delete (window as any).requestIdleCallback;
	delete (window as any).cancelIdleCallback;
});

describe('whenIdle', () => {
	it('uses requestIdleCallback with the timeout when available', () => {
		const request = vi.fn(() => 7);
		const cancel = vi.fn();
		(window as any).requestIdleCallback = request;
		(window as any).cancelIdleCallback = cancel;
		const task = vi.fn();

		const stop = whenIdle(task, 1500);
		expect(request).toHaveBeenCalledWith(task, { timeout: 1500 });
		stop();
		expect(cancel).toHaveBeenCalledWith(7);
	});

	it('falls back to a short timer and can be cancelled', () => {
		(window as any).requestIdleCallback = undefined;
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
		const task = vi.fn();
		whenIdle(task, 2000);
		vi.advanceTimersByTime(199);
		expect(task).not.toHaveBeenCalled();
		vi.advanceTimersByTime(1);
		expect(task).toHaveBeenCalledTimes(1);

		const cancelled = vi.fn();
		whenIdle(cancelled)();
		vi.advanceTimersByTime(1000);
		expect(cancelled).not.toHaveBeenCalled();
	});
});
