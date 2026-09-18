// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createVisiblePoll } from './visiblePoll';

function setHidden(hidden: boolean) {
	Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
}

describe('createVisiblePoll', () => {
	beforeEach(() => {
		vi.useFakeTimers();
		setHidden(false);
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('calls fn on the given interval while visible', async () => {
		const fn = vi.fn().mockResolvedValue(undefined);
		const poll = createVisiblePoll(fn, 1000);

		poll.start();
		expect(fn).not.toHaveBeenCalled();

		await vi.advanceTimersByTimeAsync(1000);
		expect(fn).toHaveBeenCalledTimes(1);

		await vi.advanceTimersByTimeAsync(1000);
		expect(fn).toHaveBeenCalledTimes(2);

		poll.stop();
	});

	it('does not call fn while the document is hidden', async () => {
		const fn = vi.fn().mockResolvedValue(undefined);
		const poll = createVisiblePoll(fn, 1000);

		poll.start();
		setHidden(true);
		await vi.advanceTimersByTimeAsync(5000);

		expect(fn).not.toHaveBeenCalled();
		poll.stop();
	});

	it('resumes immediately on visibilitychange rather than waiting for the next tick', async () => {
		const fn = vi.fn().mockResolvedValue(undefined);
		const poll = createVisiblePoll(fn, 1000);

		poll.start();
		setHidden(true);
		await vi.advanceTimersByTimeAsync(1000);
		expect(fn).not.toHaveBeenCalled();

		setHidden(false);
		document.dispatchEvent(new Event('visibilitychange'));
		await vi.advanceTimersByTimeAsync(0);

		expect(fn).toHaveBeenCalledTimes(1);
		poll.stop();
	});

	it('never overlaps a slow pending call with a second one', async () => {
		let resolveFn: (() => void) | undefined;
		const fn = vi.fn(
			() =>
				new Promise<void>((resolve) => {
					resolveFn = resolve;
				})
		);
		const poll = createVisiblePoll(fn, 1000);

		poll.start();
		await vi.advanceTimersByTimeAsync(1000);
		expect(fn).toHaveBeenCalledTimes(1);

		document.dispatchEvent(new Event('visibilitychange'));
		await vi.advanceTimersByTimeAsync(5000);
		expect(fn).toHaveBeenCalledTimes(1);

		resolveFn?.();
		await vi.advanceTimersByTimeAsync(0);
		await vi.advanceTimersByTimeAsync(1000);
		expect(fn).toHaveBeenCalledTimes(2);

		poll.stop();
	});

	it('clears the timer and the visibility listener on stop', async () => {
		const fn = vi.fn().mockResolvedValue(undefined);
		const removeSpy = vi.spyOn(document, 'removeEventListener');
		const poll = createVisiblePoll(fn, 1000);

		poll.start();
		poll.stop();

		expect(removeSpy).toHaveBeenCalledWith('visibilitychange', expect.any(Function));

		await vi.advanceTimersByTimeAsync(10000);
		expect(fn).not.toHaveBeenCalled();

		removeSpy.mockRestore();
	});
});
