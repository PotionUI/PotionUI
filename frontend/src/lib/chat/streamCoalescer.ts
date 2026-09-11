/**
 * Batches a rapid-fire sequence of values (the chat SSE stream's `token`
 * events) into at most one applied value per animation frame, instead of
 * applying — and re-rendering off of — every single one.
 *
 * Pure and DOM-free: the scheduler is injected (defaulting to
 * `requestAnimationFrame`, falling back to `setTimeout(fn, 16)` when it
 * isn't available — Node test environments, most notably) so this is
 * exercisable with fake timers without touching the real clock or a real
 * frame.
 */

export interface StreamCoalescer {
	/** Record `value` as the latest pending value and schedule (if not
	 * already scheduled) one flush for the next frame. Repeated calls before
	 * that frame runs replace the pending value — last write wins. */
	push(value: string): void;
	/** Apply the pending value (if any) right now and cancel the scheduled
	 * flush. No-op if nothing is pending. */
	flush(): void;
	/** Discard any pending value and cancel the scheduled flush without
	 * applying it. */
	cancel(): void;
}

type Handle = ReturnType<typeof setTimeout> | number;

function defaultSchedule(cb: () => void): Handle {
	if (typeof requestAnimationFrame === 'function') return requestAnimationFrame(cb);
	return setTimeout(cb, 16);
}

function defaultCancel(handle: Handle): void {
	if (typeof cancelAnimationFrame === 'function' && typeof requestAnimationFrame === 'function') {
		cancelAnimationFrame(handle as number);
		return;
	}
	clearTimeout(handle as ReturnType<typeof setTimeout>);
}

export interface StreamCoalescerOptions {
	schedule?: (cb: () => void) => Handle;
	cancelSchedule?: (handle: Handle) => void;
}

/**
 * `apply` is called with the most recently pushed value, at most once per
 * scheduled frame. Never called with a stale value once a newer one has been
 * pushed (last-wins), and never called after `cancel()` for whatever was
 * still pending at that point.
 */
export function createStreamCoalescer(
	apply: (value: string) => void,
	options: StreamCoalescerOptions = {}
): StreamCoalescer {
	const schedule = options.schedule ?? defaultSchedule;
	const cancelSchedule = options.cancelSchedule ?? defaultCancel;

	let pending: string | null = null;
	let handle: Handle | null = null;

	function clearHandle(): void {
		if (handle !== null) {
			cancelSchedule(handle);
			handle = null;
		}
	}

	function flush(): void {
		clearHandle();
		if (pending === null) return;
		const value = pending;
		pending = null;
		apply(value);
	}

	function cancel(): void {
		clearHandle();
		pending = null;
	}

	function push(value: string): void {
		pending = value;
		if (handle === null) {
			handle = schedule(() => {
				handle = null;
				flush();
			});
		}
	}

	return { push, flush, cancel };
}

/** Schedule `cb` for the next frame — exported so DOM-adjacent callers (a
 * scroll write, for instance) can coalesce on the same rAF/timeout fallback
 * rule without duplicating it. Returns a handle for `cancelFrame`. */
export function scheduleFrame(cb: () => void): Handle {
	return defaultSchedule(cb);
}

export function cancelFrame(handle: Handle): void {
	defaultCancel(handle);
}
