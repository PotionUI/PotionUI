import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createStreamCoalescer } from './streamCoalescer';

/**
 * `requestAnimationFrame` doesn't exist in this suite's Node test
 * environment, so `createStreamCoalescer`'s default scheduler already falls
 * through to `setTimeout(fn, 16)` — these tests drive that path directly
 * with fake timers rather than injecting a fake scheduler, so they exercise
 * the same code a real run does.
 */

beforeEach(() => {
	vi.useFakeTimers();
});

afterEach(() => {
	vi.useRealTimers();
});

describe('createStreamCoalescer', () => {
	it('applies nothing until the scheduled frame runs', () => {
		const apply = vi.fn();
		const coalescer = createStreamCoalescer(apply);

		coalescer.push('a');
		expect(apply).not.toHaveBeenCalled();

		vi.advanceTimersByTime(16);
		expect(apply).toHaveBeenCalledTimes(1);
		expect(apply).toHaveBeenCalledWith('a');
	});

	it('last-wins: multiple pushes before the frame collapse into one apply with the latest value', () => {
		const apply = vi.fn();
		const coalescer = createStreamCoalescer(apply);

		coalescer.push('a');
		coalescer.push('ab');
		coalescer.push('abc');

		vi.advanceTimersByTime(16);

		expect(apply).toHaveBeenCalledTimes(1);
		expect(apply).toHaveBeenCalledWith('abc');
	});

	it('flush() applies the pending value immediately and cancels the scheduled frame (no double-apply)', () => {
		const apply = vi.fn();
		const coalescer = createStreamCoalescer(apply);

		coalescer.push('a');
		coalescer.flush();
		expect(apply).toHaveBeenCalledTimes(1);
		expect(apply).toHaveBeenCalledWith('a');

		// The frame that was scheduled by push() must not fire a second apply
		// for the same value now that flush() already applied it.
		vi.advanceTimersByTime(100);
		expect(apply).toHaveBeenCalledTimes(1);
	});

	it('flush() with nothing pending is a no-op', () => {
		const apply = vi.fn();
		const coalescer = createStreamCoalescer(apply);
		coalescer.flush();
		expect(apply).not.toHaveBeenCalled();
	});

	it('cancel() discards the pending value — nothing is applied after it', () => {
		const apply = vi.fn();
		const coalescer = createStreamCoalescer(apply);

		coalescer.push('a');
		coalescer.cancel();

		vi.advanceTimersByTime(100);
		expect(apply).not.toHaveBeenCalled();
	});

	it('a push after cancel() schedules and applies normally again', () => {
		const apply = vi.fn();
		const coalescer = createStreamCoalescer(apply);

		coalescer.push('a');
		coalescer.cancel();
		coalescer.push('b');
		vi.advanceTimersByTime(16);

		expect(apply).toHaveBeenCalledTimes(1);
		expect(apply).toHaveBeenCalledWith('b');
	});

	it('applying flushes only once per scheduled frame even across two separate push bursts', () => {
		const apply = vi.fn();
		const coalescer = createStreamCoalescer(apply);

		coalescer.push('a');
		vi.advanceTimersByTime(16);
		coalescer.push('b');
		coalescer.push('c');
		vi.advanceTimersByTime(16);

		expect(apply).toHaveBeenCalledTimes(2);
		expect(apply).toHaveBeenNthCalledWith(1, 'a');
		expect(apply).toHaveBeenNthCalledWith(2, 'c');
	});
});
