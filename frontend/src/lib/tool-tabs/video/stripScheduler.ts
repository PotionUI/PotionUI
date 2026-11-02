import { computePeaks, MAX_DECODE_BYTES, PEAK_DECODE_SAMPLE_RATE, type Peaks } from '$lib/utils/audioPeaks';

export function stripTimes(duration: number, count: number): number[] {
	if (!Number.isFinite(duration) || duration <= 0 || count <= 0) return [];
	const cell = duration / count;
	const last = Math.max(0, duration - 0.05);
	return Array.from({ length: count }, (_, i) => Math.min(last, (i + 0.5) * cell));
}

export function fillOrder(count: number): number[] {
	if (count <= 0) return [];
	const order: number[] = [];
	const seen = new Set<number>();
	const push = (index: number) => {
		if (index < 0 || index >= count || seen.has(index)) return;
		seen.add(index);
		order.push(index);
	};
	push(0);
	push(count - 1);
	let queue: Array<[number, number]> = [[0, count - 1]];
	while (queue.length) {
		const next: Array<[number, number]> = [];
		for (const [low, high] of queue) {
			if (high - low < 2) continue;
			const mid = Math.floor((low + high) / 2);
			push(mid);
			next.push([low, mid], [mid, high]);
		}
		queue = next;
	}
	for (let i = 0; i < count; i += 1) push(i);
	return order;
}

export type StripOutcome = 'done' | 'cancelled';

export interface StripJob<T> {
	count: number;
	signal: AbortSignal;
	work: (index: number, signal: AbortSignal) => Promise<T>;
	onResult: (index: number, value: T) => void;
	onError?: (index: number, error: unknown) => void;
	pause?: () => Promise<void>;
}

export async function runStrip<T>(job: StripJob<T>): Promise<StripOutcome> {
	for (const index of fillOrder(job.count)) {
		if (job.signal.aborted) return 'cancelled';
		try {
			const value = await job.work(index, job.signal);
			if (job.signal.aborted) return 'cancelled';
			job.onResult(index, value);
		} catch (error) {
			if (job.signal.aborted) return 'cancelled';
			job.onError?.(index, error);
		}
		if (job.pause) await job.pause();
	}
	return job.signal.aborted ? 'cancelled' : 'done';
}

export interface PeakDeps {
	fetch: (url: string, init: { signal: AbortSignal }) => Promise<{ ok: boolean; headers: { get(name: string): string | null }; arrayBuffer(): Promise<ArrayBuffer> }>;
	decode: (data: ArrayBuffer) => Promise<{ numberOfChannels: number; getChannelData(index: number): ArrayLike<number> }>;
}

function browserDeps(): PeakDeps {
	return {
		fetch: (url, init) => fetch(url, { credentials: 'same-origin', signal: init.signal }),
		decode: (data) => new OfflineAudioContext(1, 1, PEAK_DECODE_SAMPLE_RATE).decodeAudioData(data)
	};
}

export async function loadPeaks(url: string, bars: number, signal: AbortSignal, deps: PeakDeps = browserDeps()): Promise<Peaks | null> {
	if (!url || bars <= 0 || signal.aborted) return null;
	try {
		const response = await deps.fetch(url, { signal });
		if (!response.ok || signal.aborted) return null;
		const announced = Number(response.headers.get('content-length') ?? 0);
		if (announced > MAX_DECODE_BYTES) return null;
		const data = await response.arrayBuffer();
		if (signal.aborted || data.byteLength > MAX_DECODE_BYTES) return null;
		const buffer = await deps.decode(data);
		if (signal.aborted) return null;
		const channels = Array.from({ length: buffer.numberOfChannels }, (_, i) => buffer.getChannelData(i));
		return computePeaks(channels, bars);
	} catch {
		return null;
	}
}
