import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMissingVideoExpiry, createPreviewKeeper, createVideoHandoff, isLiveStatus } from './videoHandoff';

const PREVIEW = 'data:image/png;base64,last-preview';
const FINAL = '/api/media/generations/gen-1/0.mp4';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function setup(timeoutMs = 1000) {
	const states: boolean[] = [];
	const handoff = createVideoHandoff((value) => states.push(value), timeoutMs);
	return { states, handoff };
}

describe('createVideoHandoff', () => {
	it('holds the preview until the visible player reports its first frame', () => {
		const { states, handoff } = setup();
		handoff.want(FINAL, true);

		expect(states).toEqual([true]);

		handoff.ready();

		expect(states).toEqual([true, false]);
	});

	it('does not hold when there was no preview to keep', () => {
		const { states, handoff } = setup();
		handoff.want(FINAL, false);

		expect(states).toEqual([false]);
	});

	it('keeps holding while the same video is asked for again', () => {
		const { states, handoff } = setup();
		handoff.want(FINAL, true);
		handoff.want(FINAL, true);
		handoff.want(FINAL, true);

		expect(states).toEqual([true]);
	});

	it('releases the hold when the video goes away', () => {
		const { states, handoff } = setup();
		handoff.want(FINAL, true);
		handoff.want(null, true);

		expect(states).toEqual([true, false]);
	});

	it('does not hold again for a video it has already shown', () => {
		const { states, handoff } = setup();
		handoff.want(FINAL, true);
		handoff.ready();
		handoff.want(FINAL, true);

		expect(states).toEqual([true, false]);
	});

	it('gives up after the timeout so a player that never paints cannot hold the preview forever', () => {
		const { states, handoff } = setup(1000);
		handoff.want(FINAL, true);

		vi.advanceTimersByTime(999);
		expect(states).toEqual([true]);

		vi.advanceTimersByTime(1);
		expect(states).toEqual([true, false]);
	});
});

describe('createPreviewKeeper', () => {
	it('keeps the last inline preview after the live preview clears', () => {
		const keep = createPreviewKeeper();

		expect(keep('gen-1', PREVIEW)).toBe(PREVIEW);
		expect(keep('gen-1', null)).toBe(PREVIEW);
		expect(keep('gen-1', FINAL)).toBe(PREVIEW);
	});

	it('forgets the preview when another generation starts', () => {
		const keep = createPreviewKeeper();
		keep('gen-1', PREVIEW);

		expect(keep('gen-2', null)).toBeNull();
	});
});

describe('video handoff across generations', () => {
	it('holds only the first video of a generation, not another video shown later', () => {
		const states: boolean[] = [];
		const handoff = createVideoHandoff((value) => states.push(value), 1000);
		handoff.want(FINAL, true, 'gen-1');
		handoff.ready();
		handoff.want('/api/media/generations/gen-1/1.mp4', true, 'gen-1');

		expect(states).toEqual([false, true, false, false]);
	});

	it('drops a pending hold when another generation is shown', () => {
		const states: boolean[] = [];
		const handoff = createVideoHandoff((value) => states.push(value), 1000);
		handoff.want(FINAL, true, 'gen-1');
		handoff.want(null, false, 'gen-2');

		expect(states).toEqual([false, true, false, false]);
		expect(states.at(-1)).toBe(false);
	});
});

describe('createMissingVideoExpiry', () => {
	it('expires the hold when a video never turns up, and re-arms for the next generation', () => {
		const states: boolean[] = [];
		const track = createMissingVideoExpiry((value) => states.push(value), 1000);
		track(true, 'gen-1');
		vi.advanceTimersByTime(1000);
		expect(states.at(-1)).toBe(true);

		track(true, 'gen-2');
		expect(states.at(-1)).toBe(false);
	});

	it('never expires when the video shows up in time', () => {
		const states: boolean[] = [];
		const track = createMissingVideoExpiry((value) => states.push(value), 1000);
		track(true, 'gen-1');
		vi.advanceTimersByTime(500);
		track(false, 'gen-1');
		vi.advanceTimersByTime(5000);

		expect(states.every((value) => value === false)).toBe(true);
	});
});

describe('isLiveStatus', () => {
	it('treats running, pending and completed as live and failures as not', () => {
		for (const status of ['pending', 'running', 'completed']) expect(isLiveStatus(status)).toBe(true);
		for (const status of ['failed', 'cancelled', 'error', undefined, null]) expect(isLiveStatus(status)).toBe(false);
	});
});
