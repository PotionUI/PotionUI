/**
 * Keeps the short hand-off between continuous generations cancellable. The
 * scheduler is deliberately UI-agnostic so a stop can invalidate a pending
 * continuation without touching the generation that is currently running.
 */
export function createContinuousGenerationScheduler(onContinue: () => void, delayMs = 1_000) {
	let armed = false;
	let disposed = false;
	let timeout: ReturnType<typeof setTimeout> | null = null;

	function clearPending() {
		if (timeout !== null) {
			clearTimeout(timeout);
			timeout = null;
		}
	}

	function arm() {
		if (!disposed) armed = true;
	}

	function disarm() {
		armed = false;
		clearPending();
	}

	function schedule() {
		if (!armed || disposed || timeout !== null) return false;

		timeout = setTimeout(() => {
			timeout = null;
			if (armed && !disposed) onContinue();
		}, delayMs);
		return true;
	}

	function dispose() {
		disarm();
		disposed = true;
	}

	return {
		arm,
		disarm,
		schedule,
		dispose
	};
}
