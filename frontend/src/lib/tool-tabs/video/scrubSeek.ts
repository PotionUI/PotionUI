export interface SeekTarget {
	currentTime: number;
	seeking: boolean;
	pause(): void;
}

export interface ScrubSeekOptions {
	video: () => SeekTarget | null;
	duration: () => number;
	setPlayhead: (seconds: number) => void;
	storePlayhead: (seconds: number) => void;
	hidden?: () => boolean;
	frame?: (run: () => void) => number;
	cancelFrame?: (handle: number) => void;
	delayMs?: number;
}

export interface ScrubSeek {
	scrub(seconds: number): void;
	seekNow(seconds: number): void;
	commit(seconds: number): void;
	schedule(seconds: number): void;
	flush(): void;
	pending(): boolean;
	pendingPlayhead(): number | null;
	dispose(): number | null;
}

export function createScrubSeek(options: ScrubSeekOptions): ScrubSeek {
	const delay = options.delayMs ?? 500;
	const hidden = options.hidden ?? (() => typeof document !== 'undefined' && document.hidden);
	const frame = options.frame ?? ((run) => requestAnimationFrame(run));
	const cancelFrame = options.cancelFrame ?? ((handle) => cancelAnimationFrame(handle));
	let target: number | null = null;
	let handle = 0;
	let timer: ReturnType<typeof setTimeout> | null = null;
	let held: number | null = null;

	const clamp = (seconds: number) => Math.max(0, Math.min(options.duration() || 0, seconds));

	function apply() {
		const video = options.video();
		if (!video || target === null) {
			target = null;
			return;
		}
		const next = target;
		target = null;
		video.currentTime = next;
	}

	function tick() {
		handle = 0;
		if (target === null) return;
		apply();
		if (!hidden()) handle = frame(tick);
	}

	function cancelPending() {
		if (handle) cancelFrame(handle);
		handle = 0;
		target = null;
	}

	function commit(seconds: number) {
		if (timer) clearTimeout(timer);
		timer = null;
		held = null;
		options.storePlayhead(seconds);
	}

	return {
		scrub(seconds) {
			const video = options.video();
			video?.pause();
			const next = clamp(seconds);
			options.setPlayhead(next);
			if (!video || !Number.isFinite(next)) return;
			target = next;
			if (handle) return;
			apply();
			if (!hidden()) handle = frame(tick);
		},
		seekNow(seconds) {
			cancelPending();
			const next = clamp(seconds);
			options.setPlayhead(next);
			const video = options.video();
			if (video && Number.isFinite(next)) video.currentTime = next;
		},
		commit,
		schedule(seconds) {
			if (timer) clearTimeout(timer);
			held = seconds;
			timer = setTimeout(() => commit(seconds), delay);
		},
		flush() {
			if (held !== null) commit(held);
		},
		pending: () => target !== null,
		pendingPlayhead: () => held,
		dispose() {
			cancelPending();
			if (timer) clearTimeout(timer);
			timer = null;
			const left = held;
			held = null;
			return left;
		}
	};
}
