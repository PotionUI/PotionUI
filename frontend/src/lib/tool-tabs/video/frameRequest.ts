export interface FrameSlot {
	key: string;
	url: string;
}

export interface FrameRequestOptions {
	key: string;
	delay?: number;
	load: (signal: AbortSignal) => Promise<Blob>;
	current: () => FrameSlot | null;
	assign: (slot: FrameSlot) => void;
	createUrl?: (blob: Blob) => string;
	revokeUrl?: (url: string) => void;
	onError?: (error: unknown) => void;
}

export const FRAME_DEBOUNCE_MS = 250;

export function requestFrame(options: FrameRequestOptions): () => void {
	const controller = new AbortController();
	const createUrl = options.createUrl ?? ((blob: Blob) => URL.createObjectURL(blob));
	const revokeUrl = options.revokeUrl ?? ((url: string) => URL.revokeObjectURL(url));
	const timer = setTimeout(async () => {
		try {
			const blob = await options.load(controller.signal);
			if (controller.signal.aborted) return;
			const previous = options.current();
			options.assign({ key: options.key, url: createUrl(blob) });
			if (previous) revokeUrl(previous.url);
		} catch (error) {
			if (!controller.signal.aborted) options.onError?.(error);
		}
	}, options.delay ?? FRAME_DEBOUNCE_MS);
	return () => {
		clearTimeout(timer);
		controller.abort();
	};
}
