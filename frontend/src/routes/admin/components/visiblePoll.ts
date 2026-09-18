export interface VisiblePoll {
	start(): void;
	stop(): void;
}

export function createVisiblePoll(fn: () => void | Promise<void>, intervalMs: number): VisiblePoll {
	let running = false;
	let inFlight = false;
	let timeoutHandle: ReturnType<typeof setTimeout> | null = null;
	let visibilityHandler: (() => void) | null = null;

	function clearTimer() {
		if (timeoutHandle !== null) {
			clearTimeout(timeoutHandle);
			timeoutHandle = null;
		}
	}

	function scheduleNext() {
		clearTimer();
		timeoutHandle = setTimeout(tick, intervalMs);
	}

	async function tick() {
		if (!running || document.hidden || inFlight) return;
		inFlight = true;
		try {
			await fn();
		} finally {
			inFlight = false;
			if (running) scheduleNext();
		}
	}

	function start() {
		running = true;
		if (!visibilityHandler) {
			visibilityHandler = () => {
				if (running && !document.hidden) {
					clearTimer();
					void tick();
				}
			};
			document.addEventListener('visibilitychange', visibilityHandler);
		}
		scheduleNext();
	}

	function stop() {
		running = false;
		clearTimer();
		if (visibilityHandler) {
			document.removeEventListener('visibilitychange', visibilityHandler);
			visibilityHandler = null;
		}
	}

	return { start, stop };
}
