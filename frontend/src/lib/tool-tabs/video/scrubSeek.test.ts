import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createScrubSeek, type SeekTarget } from './scrubSeek';

function setup(options: { hidden?: boolean; duration?: number } = {}) {
	const writes: number[] = [];
	let currentTime = 0;
	const video: SeekTarget & { paused: number } = {
		paused: 0,
		seeking: false,
		pause() {
			this.paused += 1;
		},
		get currentTime() {
			return currentTime;
		},
		set currentTime(value: number) {
			writes.push(value);
			currentTime = value;
		}
	};
	const frames: Array<() => void> = [];
	const played: number[] = [];
	const stored: number[] = [];
	const state = { hidden: options.hidden ?? false };
	const seek = createScrubSeek({
		video: () => video,
		duration: () => options.duration ?? 10,
		setPlayhead: (value) => played.push(value),
		storePlayhead: (value) => stored.push(value),
		hidden: () => state.hidden,
		frame: (run) => frames.push(run),
		cancelFrame: () => frames.splice(0)
	});
	const runFrame = () => frames.splice(0).forEach((run) => run());
	return { seek, video, writes, played, stored, frames, runFrame, state };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('scrub seeking', () => {
	it('seeks on the first move straight after pausing and coalesces the rest to one per frame', () => {
		const { seek, video, writes, runFrame } = setup();
		seek.scrub(1);
		expect(video.paused).toBe(1);
		expect(writes).toEqual([1]);
		seek.scrub(2);
		seek.scrub(3);
		expect(writes).toEqual([1]);
		runFrame();
		expect(writes).toEqual([1, 3]);
		runFrame();
		expect(writes).toEqual([1, 3]);
	});

	it('lands on the exact last position when the drag stops', () => {
		const { seek, writes, runFrame, video } = setup();
		seek.scrub(1);
		seek.scrub(4.25);
		runFrame();
		expect(writes[writes.length - 1]).toBe(4.25);
		expect(video.currentTime).toBe(4.25);
		expect(seek.pending()).toBe(false);
	});

	it('seeks synchronously while the page is hidden so no frame is ever waited for', () => {
		const { seek, writes, frames } = setup({ hidden: true });
		seek.scrub(1);
		seek.scrub(2);
		seek.scrub(3);
		expect(writes).toEqual([1, 2, 3]);
		expect(frames).toHaveLength(0);
		expect(seek.pending()).toBe(false);
	});

	it('a direct seek drops a queued scrub target', () => {
		const { seek, writes, runFrame } = setup();
		seek.scrub(1);
		seek.scrub(2);
		seek.seekNow(5);
		runFrame();
		expect(writes).toEqual([1, 5]);
	});

	it('clamps to the clip', () => {
		const { seek, writes } = setup({ duration: 4 });
		seek.scrub(9);
		expect(writes).toEqual([4]);
	});

	it('reports a queued target so the async pause event does not read the stale clock', () => {
		const { seek, played, video } = setup();
		seek.scrub(1);
		seek.scrub(6);
		expect(seek.pending()).toBe(true);
		expect(video.currentTime).toBe(1);
		expect(played[played.length - 1]).toBe(6);
	});
});

describe('playhead store', () => {
	it('writes the playhead 500 ms after the last move, once', () => {
		const { seek, stored } = setup();
		seek.schedule(1);
		vi.advanceTimersByTime(300);
		seek.schedule(2);
		vi.advanceTimersByTime(499);
		expect(stored).toEqual([]);
		vi.advanceTimersByTime(1);
		expect(stored).toEqual([2]);
		vi.advanceTimersByTime(2000);
		expect(stored).toEqual([2]);
	});

	it('flush writes a held value at once and clears the timer', () => {
		const { seek, stored } = setup();
		seek.schedule(3);
		seek.flush();
		expect(stored).toEqual([3]);
		vi.advanceTimersByTime(1000);
		expect(stored).toEqual([3]);
		seek.flush();
		expect(stored).toEqual([3]);
	});

	it('commit clears a held value, and dispose hands back what was still held', () => {
		const { seek, stored } = setup();
		seek.schedule(3);
		seek.commit(4);
		expect(stored).toEqual([4]);
		expect(seek.pendingPlayhead()).toBeNull();
		seek.schedule(7);
		expect(seek.dispose()).toBe(7);
		vi.advanceTimersByTime(1000);
		expect(stored).toEqual([4]);
	});
});
