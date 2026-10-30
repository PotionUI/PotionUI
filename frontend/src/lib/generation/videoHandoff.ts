export const VIDEO_HANDOFF_TIMEOUT_MS = 8000;
export const VIDEO_MISSING_GRACE_MS = 4000;

const LIVE_STATUSES = new Set(['pending', 'queued', 'running', 'processing', 'completed']);

export function isLiveStatus(status: string | null | undefined): boolean {
	return !!status && LIVE_STATUSES.has(status);
}

export function createPreviewKeeper() {
	let generationId: string | null | undefined;
	let held: string | null = null;
	return (id: string | null | undefined, image: string | null | undefined): string | null => {
		if (id !== generationId) {
			generationId = id;
			held = null;
		}
		if (image && image.startsWith('data:')) held = image;
		return held;
	};
}

export function createMissingVideoExpiry(setExpired: (expired: boolean) => void, graceMs: number = VIDEO_MISSING_GRACE_MS) {
	let timer: ReturnType<typeof setTimeout> | null = null;
	let key: string | null | undefined;

	function clear() {
		if (timer !== null) clearTimeout(timer);
		timer = null;
	}

	return (waiting: boolean, generationId: string | null | undefined): void => {
		if (generationId !== key) {
			key = generationId;
			clear();
			setExpired(false);
		}
		if (!waiting) {
			clear();
			setExpired(false);
			return;
		}
		if (timer !== null) return;
		timer = setTimeout(() => {
			timer = null;
			setExpired(true);
		}, graceMs);
	};
}

export function createVideoHandoff(
	setPending: (pending: boolean) => void,
	timeoutMs: number = VIDEO_HANDOFF_TIMEOUT_MS
) {
	let settled: string | null | undefined;
	let waiting: string | null = null;
	let timer: ReturnType<typeof setTimeout> | null = null;
	let key: string | null | undefined;

	function clearTimer() {
		if (timer !== null) clearTimeout(timer);
		timer = null;
	}

	function ready() {
		if (waiting !== null) settled = waiting;
		waiting = null;
		clearTimer();
		setPending(false);
	}

	function want(wanted: string | null | undefined, previewHeld: boolean, generationId?: string | null) {
		if (generationId !== key) {
			key = generationId;
			settled = null;
			waiting = null;
			clearTimer();
			setPending(false);
		}
		if (!wanted) {
			clearTimer();
			waiting = null;
			setPending(false);
			return;
		}
		if (wanted === settled || wanted === waiting) return;
		clearTimer();
		waiting = null;
		if (!previewHeld || settled) {
			setPending(false);
			return;
		}
		waiting = wanted;
		setPending(true);
		timer = setTimeout(ready, timeoutMs);
	}

	return { want, ready };
}
