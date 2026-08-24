/**
 * Polls until a single `check()` reports the server healthy again, or an
 * overall wall-clock budget is exhausted - used after actions that restart a
 * backend process (`poll_health_after`) to detect when it's actually back.
 *
 * Deliberately budgets by elapsed time, not attempt count: a `check()` that
 * hangs (e.g. a connection stuck mid-restart) must not be able to blow the
 * budget out to "forever" by eating each attempt's time silently - the caller
 * is expected to pass a `check()` with its own short per-call timeout, and
 * this function additionally races each check against `intervalMs` so one
 * slow attempt can't stall the whole poll.
 */
export interface HealthPollOptions {
	/** Resolves true if healthy, false (or rejects) otherwise. Never let this
	 * hang indefinitely - give it its own timeout. */
	check: () => Promise<boolean>;
	/** Wait this long before the first check, so the restart has time to
	 * actually kill the old process before we might observe it as "healthy". */
	initialDelayMs?: number;
	/** Wait this long between checks. */
	intervalMs?: number;
	/** Give up (resolve false) after this much total wall time. */
	timeoutMs?: number;
	/** Injectable for tests; defaults to a real setTimeout-based sleep. */
	sleep?: (ms: number) => Promise<void>;
	/** Injectable for tests; defaults to Date.now. */
	now?: () => number;
}

function defaultSleep(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Races `check()` against `ms`, resolving `{ healthy: false, timedOut: true }`
 * (never rejecting) if the timeout wins - a hung `check()` must look like
 * "not healthy yet", not blow up the poll loop or stall the whole budget. */
function raceCheck(
	check: () => Promise<boolean>,
	ms: number,
	sleep: (ms: number) => Promise<void>
): Promise<{ healthy: boolean; timedOut: boolean }> {
	return Promise.race([
		check()
			.then((healthy) => ({ healthy, timedOut: false }))
			.catch(() => ({ healthy: false, timedOut: false })),
		sleep(ms).then(() => ({ healthy: false, timedOut: true }))
	]);
}

export async function waitForHealthy(options: HealthPollOptions): Promise<boolean> {
	const {
		check,
		initialDelayMs = 1500,
		intervalMs = 1000,
		timeoutMs = 120000,
		sleep = defaultSleep,
		now = Date.now
	} = options;

	await sleep(initialDelayMs);

	const deadline = now() + timeoutMs;
	while (now() < deadline) {
		const { healthy, timedOut } = await raceCheck(check, intervalMs, sleep);
		if (healthy) return true;
		// A hung check already spent `intervalMs` racing the timeout - don't
		// also sleep afterward, or a stuck connection paces at 2x intervalMs.
		if (!timedOut) await sleep(intervalMs);
	}
	return false;
}
