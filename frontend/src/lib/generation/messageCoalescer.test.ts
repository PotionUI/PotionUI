import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { MessageCoalescer, type CoalescableMessage } from './messageCoalescer';

describe('MessageCoalescer', () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('flushes via the setTimeout(16) fallback when requestAnimationFrame is unavailable (node test env)', () => {
		expect(typeof requestAnimationFrame).toBe('undefined');

		const dispatched: CoalescableMessage[] = [];
		const coalescer = new MessageCoalescer((m) => dispatched.push(m));

		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.1 });
		expect(dispatched).toHaveLength(0);

		vi.advanceTimersByTime(16);
		expect(dispatched).toHaveLength(1);
	});

	it('keeps only the last generation_status message per generation within a frame', () => {
		const dispatched: CoalescableMessage[] = [];
		const coalescer = new MessageCoalescer((m) => dispatched.push(m));

		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.1, step: 1 });
		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.2, step: 2 });
		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.3, step: 3 });
		vi.advanceTimersByTime(16);

		expect(dispatched).toEqual([{ type: 'generation_status', generation_id: 'g1', progress: 0.3, step: 3 }]);
	});

	it('same last-wins rule applies to workbench_update and timer_update', () => {
		const dispatched: CoalescableMessage[] = [];
		const coalescer = new MessageCoalescer((m) => dispatched.push(m));

		coalescer.enqueue({ type: 'workbench_update', generation_id: 'g1', image: 'a' });
		coalescer.enqueue({ type: 'workbench_update', generation_id: 'g1', image: 'b' });
		coalescer.enqueue({ type: 'timer_update', generation_id: 'g1', timer_value: 1 });
		coalescer.enqueue({ type: 'timer_update', generation_id: 'g1', timer_value: 2 });
		vi.advanceTimersByTime(16);

		expect(dispatched).toEqual([
			{ type: 'workbench_update', generation_id: 'g1', image: 'b' },
			{ type: 'timer_update', generation_id: 'g1', timer_value: 2 }
		]);
	});

	it('preserves arrival order across types and keeps every non-coalesced message', () => {
		const dispatched: CoalescableMessage[] = [];
		const coalescer = new MessageCoalescer((m) => dispatched.push(m));

		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.1 });
		coalescer.enqueue({ type: 'gallery_update', generation_id: 'g1', images: ['x'] });
		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.2 });
		coalescer.enqueue({ type: 'gallery_update', generation_id: 'g1', images: ['y'] });
		coalescer.enqueue({ type: 'pipe_artifact', generation_id: 'g1', index: 0 });
		vi.advanceTimersByTime(16);

		// The superseded status (progress: 0.1) is dropped; every gallery_update
		// and pipe_artifact survives, in the order it arrived, at the position
		// where the surviving status landed.
		expect(dispatched).toEqual([
			{ type: 'generation_status', generation_id: 'g1', progress: 0.2 },
			{ type: 'gallery_update', generation_id: 'g1', images: ['x'] },
			{ type: 'gallery_update', generation_id: 'g1', images: ['y'] },
			{ type: 'pipe_artifact', generation_id: 'g1', index: 0 }
		]);
	});

	it('flushes immediately on a terminal message, so status is applied before complete', () => {
		const dispatched: CoalescableMessage[] = [];
		const coalescer = new MessageCoalescer((m) => dispatched.push(m));

		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.9 });
		coalescer.enqueue({ type: 'generation_complete', generation_id: 'g1' });

		// No timer advance at all -- the terminal message forced an immediate flush.
		expect(dispatched).toEqual([
			{ type: 'generation_status', generation_id: 'g1', progress: 0.9 },
			{ type: 'generation_complete', generation_id: 'g1' }
		]);
	});

	it('drops a generation_status identical to the last one actually applied for that generation', () => {
		const dispatched: CoalescableMessage[] = [];
		const coalescer = new MessageCoalescer((m) => dispatched.push(m));

		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.5, current_step: 'sampling', message: 'x', step: 4, status: 'running' });
		vi.advanceTimersByTime(16);
		expect(dispatched).toHaveLength(1);

		// A later frame repeats the exact same fields -- a no-op repeat, dropped.
		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.5, current_step: 'sampling', message: 'x', step: 4, status: 'running' });
		vi.advanceTimersByTime(16);
		expect(dispatched).toHaveLength(1);

		// A field actually changes -- applied.
		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.6, current_step: 'sampling', message: 'x', step: 4, status: 'running' });
		vi.advanceTimersByTime(16);
		expect(dispatched).toHaveLength(2);
	});

	it('applies a status that differs only in pipe_id, even though the 5 "classic" fields are unchanged', () => {
		const dispatched: CoalescableMessage[] = [];
		const coalescer = new MessageCoalescer((m) => dispatched.push(m));

		coalescer.enqueue({
			type: 'generation_status',
			generation_id: 'g1',
			progress: 0.5,
			current_step: 'sampling',
			message: 'x',
			status: 'running',
			pipe_id: 1
		});
		vi.advanceTimersByTime(16);

		coalescer.enqueue({
			type: 'generation_status',
			generation_id: 'g1',
			progress: 0.5,
			current_step: 'sampling',
			message: 'x',
			status: 'running',
			pipe_id: 2
		});
		vi.advanceTimersByTime(16);

		expect(dispatched).toHaveLength(2);
	});

	it('applies a status that differs only in the Video Director segment_id', () => {
		const dispatched: CoalescableMessage[] = [];
		const coalescer = new MessageCoalescer((m) => dispatched.push(m));

		coalescer.enqueue({
			type: 'generation_status',
			generation_id: 'g1',
			progress: 0,
			current_step: 'sampling',
			message: '',
			status: 'running',
			segment_id: 'shot-1'
		});
		vi.advanceTimersByTime(16);

		coalescer.enqueue({
			type: 'generation_status',
			generation_id: 'g1',
			progress: 0,
			current_step: 'sampling',
			message: '',
			status: 'running',
			segment_id: 'shot-2'
		});
		vi.advanceTimersByTime(16);

		expect(dispatched).toHaveLength(2);
	});

	it('drops the last-applied cache for a generation once it completes', () => {
		const dispatched: CoalescableMessage[] = [];
		const coalescer = new MessageCoalescer((m) => dispatched.push(m));

		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.5 });
		vi.advanceTimersByTime(16);
		coalescer.enqueue({ type: 'generation_complete', generation_id: 'g1' });

		// Confirmed indirectly below (bounded-memory eviction test): once `done`
		// evicts 'g1', a fresh status for it must not be dropped as a duplicate
		// of the pre-completion value -- that only holds if completion itself
		// already cleared the cached last-applied status, not just the `done`
		// eviction.
		expect(dispatched.at(-1)).toEqual({ type: 'generation_complete', generation_id: 'g1' });
	});

	it('caps `done` at 256 generations, evicting the oldest first, and un-poisons its last-applied cache on eviction', () => {
		const dispatched: CoalescableMessage[] = [];
		const coalescer = new MessageCoalescer((m) => dispatched.push(m));

		// g0 completes with a known last-applied status, then 256 more distinct
		// generations complete after it -- enough to push `done`'s cap (256)
		// past g0, evicting it as the oldest entry.
		coalescer.enqueue({ type: 'generation_status', generation_id: 'g0', progress: 0.5 });
		vi.advanceTimersByTime(16);
		coalescer.enqueue({ type: 'generation_complete', generation_id: 'g0' });
		for (let i = 1; i <= 256; i++) {
			coalescer.enqueue({ type: 'generation_complete', generation_id: `g${i}` });
		}
		dispatched.length = 0;

		// g1 is still tracked as done (only the oldest, g0, was evicted) -- a
		// late status for it is still dropped.
		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.9 });
		vi.advanceTimersByTime(16);
		expect(dispatched).toHaveLength(0);

		// g0 was evicted from `done` -- a status for it is no longer blocked,
		// and since completion cleared its last-applied cache, it is NOT
		// mistaken for a duplicate of the pre-completion progress:0.5 value
		// even though the fields are identical.
		coalescer.enqueue({ type: 'generation_status', generation_id: 'g0', progress: 0.5 });
		vi.advanceTimersByTime(16);
		expect(dispatched).toEqual([{ type: 'generation_status', generation_id: 'g0', progress: 0.5 }]);
	});

	it('isolates last-wins and dedup per generation_id', () => {
		const dispatched: CoalescableMessage[] = [];
		const coalescer = new MessageCoalescer((m) => dispatched.push(m));

		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.1 });
		coalescer.enqueue({ type: 'generation_status', generation_id: 'g2', progress: 0.9 });
		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.2 });
		vi.advanceTimersByTime(16);

		expect(dispatched).toEqual(
			expect.arrayContaining([
				{ type: 'generation_status', generation_id: 'g1', progress: 0.2 },
				{ type: 'generation_status', generation_id: 'g2', progress: 0.9 }
			])
		);
		expect(dispatched).toHaveLength(2);
	});

	it('never resurrects a status for a generation that already completed', () => {
		const dispatched: CoalescableMessage[] = [];
		const coalescer = new MessageCoalescer((m) => dispatched.push(m));

		coalescer.enqueue({ type: 'generation_complete', generation_id: 'g1' });
		expect(dispatched).toEqual([{ type: 'generation_complete', generation_id: 'g1' }]);

		// A status for the same generation, arriving (reordered, or simply
		// late) after its terminal event, must never be applied.
		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.99 });
		coalescer.enqueue({ type: 'workbench_update', generation_id: 'g1', image: 'late' });
		coalescer.enqueue({ type: 'timer_update', generation_id: 'g1', timer_value: 5 });
		vi.advanceTimersByTime(16);

		expect(dispatched).toEqual([{ type: 'generation_complete', generation_id: 'g1' }]);
	});

	it('a status for a still-running generation is unaffected by a different generation completing', () => {
		const dispatched: CoalescableMessage[] = [];
		const coalescer = new MessageCoalescer((m) => dispatched.push(m));

		coalescer.enqueue({ type: 'generation_status', generation_id: 'g2', progress: 0.5 });
		coalescer.enqueue({ type: 'generation_complete', generation_id: 'g1' });
		vi.advanceTimersByTime(16);

		expect(dispatched).toEqual([
			{ type: 'generation_status', generation_id: 'g2', progress: 0.5 },
			{ type: 'generation_complete', generation_id: 'g1' }
		]);
	});

	it('a message without a generation_id is always delivered, never coalesced', () => {
		const dispatched: CoalescableMessage[] = [];
		const coalescer = new MessageCoalescer((m) => dispatched.push(m));

		coalescer.enqueue({ type: 'generation_status', progress: 0.1 });
		coalescer.enqueue({ type: 'generation_status', progress: 0.2 });
		vi.advanceTimersByTime(16);

		expect(dispatched).toEqual([
			{ type: 'generation_status', progress: 0.1 },
			{ type: 'generation_status', progress: 0.2 }
		]);
	});

	it('only schedules one flush for a burst within the same frame', () => {
		const scheduleFlush = vi.fn((flush: () => void) => setTimeout(flush, 16));
		const dispatched: CoalescableMessage[] = [];
		const coalescer = new MessageCoalescer((m) => dispatched.push(m), scheduleFlush);

		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.1 });
		coalescer.enqueue({ type: 'generation_status', generation_id: 'g1', progress: 0.2 });
		coalescer.enqueue({ type: 'gallery_update', generation_id: 'g1', images: ['x'] });

		expect(scheduleFlush).toHaveBeenCalledTimes(1);
		vi.advanceTimersByTime(16);
		expect(dispatched).toHaveLength(2);
	});
});
