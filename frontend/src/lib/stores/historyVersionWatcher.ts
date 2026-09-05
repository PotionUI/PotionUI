// Change detection for the history page. The page used to refetch the whole
// first page on a fixed timer just to find out whether anything had changed;
// this asks the server for a token instead and refetches only when it moves.
//
// The token is the server's per-user history revision, bumped by the mutation
// paths it knows about, so it is a signal and not a proof of completeness. The
// recovery refresh below is the convergence guarantee: a mutation nobody bumped
// still lands within one recovery interval.

export const HISTORY_VERSION_INTERVAL_MS = 8000;
export const HISTORY_VERSION_JITTER = 0.25;
export const HISTORY_CHANGE_DEBOUNCE_MS = 400;
export const HISTORY_RECOVERY_INTERVAL_MS = 60000;

export interface HistoryVersionWatcherOptions {
	// Resolves to the server's token, or null when the request failed — a
	// failure must not be mistaken for "the history changed".
	fetchVersion: () => Promise<string | null>;
	// Issue the silent, merging refetch of the current page.
	reload: () => void;
	// Discovery only makes sense where a generation started elsewhere would be
	// visible: page 1 of the created_at-desc list, as with the poll it replaces.
	canDiscover?: () => boolean;
	isVisible?: () => boolean;
	intervalMs?: number;
	jitter?: number;
	changeDebounceMs?: number;
	// The convergence guarantee, not a trigger: the token is bumped explicitly
	// by the mutation paths the server knows about, so anything it cannot see
	// still lands within one of these.
	recoveryIntervalMs?: number;
	random?: () => number;
}

export interface HistoryVersionWatcher {
	start(): void;
	stop(): void;
	// An immediate check, for the moments where events may have been missed.
	check(): Promise<void>;
	// The caller already knows the list changed; rebaseline and refetch. A burst
	// of events collapses into one.
	notifyChanged(): void;
	setVisible(visible: boolean): void;
	connectionChanged(connected: boolean): void;
}

export function createHistoryVersionWatcher(
	options: HistoryVersionWatcherOptions
): HistoryVersionWatcher {
	const {
		fetchVersion,
		reload,
		canDiscover = () => true,
		isVisible = () => true,
		intervalMs = HISTORY_VERSION_INTERVAL_MS,
		jitter = HISTORY_VERSION_JITTER,
		changeDebounceMs = HISTORY_CHANGE_DEBOUNCE_MS,
		recoveryIntervalMs = HISTORY_RECOVERY_INTERVAL_MS,
		random = Math.random
	} = options;

	let timer: ReturnType<typeof setTimeout> | undefined;
	let recoveryTimer: ReturnType<typeof setTimeout> | undefined;
	let changeTimer: ReturnType<typeof setTimeout> | undefined;
	let inFlight: Promise<string | null> | null = null;
	let lastSeen: string | null = null;
	let everConnected = false;
	let stopped = true;

	// Overlapping callers (a tick, a focus, a reconnect) share one request.
	function pull(): Promise<string | null> {
		if (!inFlight) {
			inFlight = fetchVersion().then(
				(token) => {
					inFlight = null;
					return token;
				},
				() => {
					inFlight = null;
					return null;
				}
			);
		}
		return inFlight;
	}

	// Jitter spreads the herd of tabs that all opened history at the same moment.
	function nextDelay(base: number): number {
		return Math.round(base * (1 + (random() * 2 - 1) * jitter));
	}

	function schedule(): void {
		if (stopped || timer !== undefined) return;
		timer = setTimeout(tick, nextDelay(intervalMs));
	}

	function scheduleRecovery(): void {
		if (stopped || recoveryTimer !== undefined) return;
		recoveryTimer = setTimeout(recoveryTick, nextDelay(recoveryIntervalMs));
	}

	function clear(): void {
		if (timer === undefined) return;
		clearTimeout(timer);
		timer = undefined;
	}

	function clearRecovery(): void {
		if (recoveryTimer === undefined) return;
		clearTimeout(recoveryTimer);
		recoveryTimer = undefined;
	}

	function clearChange(): void {
		if (changeTimer === undefined) return;
		clearTimeout(changeTimer);
		changeTimer = undefined;
	}

	function tick(): void {
		timer = undefined;
		schedule();
		void check();
	}

	function recoveryTick(): void {
		recoveryTimer = undefined;
		scheduleRecovery();
		if (!canDiscover()) return;
		void refresh();
	}

	async function check(): Promise<void> {
		if (stopped || !isVisible()) return;
		if (!canDiscover()) {
			// Off page 1 there is nothing to discover, so drop the baseline: the
			// next check re-establishes one instead of reporting the accumulated
			// difference as a change.
			lastSeen = null;
			return;
		}

		const token = await pull();
		if (stopped || token === null) return;

		const changed = lastSeen !== null && token !== lastSeen;
		lastSeen = token;
		if (changed) reload();
	}

	async function refresh(): Promise<void> {
		// Baseline before refetching, never after: the token is then no newer
		// than the rows that arrive, so a write landing in between still reads
		// as a difference on the next check.
		const token = await pull();
		if (stopped) return;
		if (token !== null) lastSeen = token;
		reload();
	}

	function notifyChanged(): void {
		if (stopped) return;
		clearChange();
		changeTimer = setTimeout(() => {
			changeTimer = undefined;
			void refresh();
		}, changeDebounceMs);
	}

	function setVisible(visible: boolean): void {
		if (stopped) return;
		if (!visible) {
			clear();
			clearRecovery();
			return;
		}
		schedule();
		scheduleRecovery();
		void check();
	}

	return {
		start(): void {
			stopped = false;
			if (!isVisible()) return;
			schedule();
			scheduleRecovery();
			void check();
		},

		stop(): void {
			stopped = true;
			clear();
			clearRecovery();
			clearChange();
		},

		check,
		notifyChanged,
		setVisible,

		connectionChanged(connected: boolean): void {
			if (!connected) return;
			// The first connection carries no backlog; a later one may have
			// missed events while the socket was down.
			if (everConnected) void check();
			everConnected = true;
		}
	};
}
