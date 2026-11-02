export interface ClockTick {
	mediaTime: number;
	interval: number;
}

type FrameCallbackVideo = HTMLVideoElement & {
	requestVideoFrameCallback?: (callback: (now: number, meta: { mediaTime: number }) => void) => number;
	cancelVideoFrameCallback?: (handle: number) => void;
};

export function followFrames(video: HTMLVideoElement, onFrame: (tick: ClockTick) => void): () => void {
	const el = video as FrameCallbackVideo;
	let stopped = false;
	let last = 0;
	let handle = 0;
	const emit = (now: number, mediaTime: number) => {
		const interval = last ? now - last : 0;
		last = now;
		onFrame({ mediaTime, interval });
	};
	if (typeof el.requestVideoFrameCallback === 'function') {
		const tick = (now: number, meta: { mediaTime: number }) => {
			if (stopped) return;
			emit(now, meta.mediaTime);
			handle = el.requestVideoFrameCallback!(tick);
		};
		handle = el.requestVideoFrameCallback(tick);
		return () => {
			stopped = true;
			el.cancelVideoFrameCallback?.(handle);
		};
	}
	const tick = (now: number) => {
		if (stopped) return;
		if (!video.paused) emit(now, video.currentTime);
		handle = requestAnimationFrame(tick);
	};
	handle = requestAnimationFrame(tick);
	return () => {
		stopped = true;
		cancelAnimationFrame(handle);
	};
}
