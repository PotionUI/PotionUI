import { describe, expect, it, vi } from 'vitest';
import {
	createVideoGroup,
	finiteDuration,
	formatClock,
	longestDuration,
	needsSeek,
	targetTime,
	type VideoLike
} from './videoSync';

function fake(duration: number, over: Partial<VideoLike> = {}): VideoLike & { plays: number; pauses: number } {
	const state = {
		currentTime: 0,
		duration,
		paused: true,
		muted: false,
		loop: false,
		plays: 0,
		pauses: 0,
		play() {
			state.plays += 1;
			state.paused = false;
			return Promise.resolve();
		},
		pause() {
			state.pauses += 1;
			state.paused = true;
		},
		...over
	};
	return state;
}

describe('sync maths', () => {
	it('uses the longest finite duration', () => {
		expect(longestDuration([{ duration: 2.4 }, { duration: 5.2 }, { duration: Number.NaN }])).toBe(5.2);
		expect(longestDuration([])).toBe(0);
		expect(finiteDuration({ duration: Infinity })).toBe(0);
	});

	it('holds a short clip on its last frame while the longest keeps going', () => {
		expect(targetTime(1.2, 5)).toBe(1.2);
		expect(targetTime(4.8, 2.4)).toBe(2.4);
		expect(targetTime(-1, 5)).toBe(0);
		expect(targetTime(3, 0)).toBe(0);
	});

	it('only seeks past the drift tolerance', () => {
		expect(needsSeek(1.0, 1.05)).toBe(false);
		expect(needsSeek(1.0, 1.3)).toBe(true);
	});

	it('formats the clock as mm:ss.t', () => {
		expect(formatClock(2.4)).toBe('00:02.4');
		expect(formatClock(65.96)).toBe('01:05.9');
		expect(formatClock(Number.NaN)).toBe('00:00.0');
	});
});

describe('video group', () => {
	it('starts playing muted and looping each clip when unsynced', () => {
		const group = createVideoGroup();
		const a = fake(2);
		group.add(a);
		expect(a.muted).toBe(true);
		expect(a.plays).toBe(1);
		group.setSynced(false);
		expect(a.loop).toBe(true);
	});

	it('does not loop natively while synced so the longest clip drives the loop', () => {
		const group = createVideoGroup();
		const a = fake(2);
		group.add(a);
		expect(a.loop).toBe(false);
	});

	it('pauses and resumes every visible clip', () => {
		const group = createVideoGroup();
		const a = fake(2);
		const b = fake(4);
		group.add(a);
		group.add(b);
		group.pause();
		expect([a.paused, b.paused]).toEqual([true, true]);
		group.play();
		expect([a.paused, b.paused]).toEqual([false, false]);
		expect(group.state().playing).toBe(true);
	});

	it('pauses an off-screen clip and plays it again when it returns', () => {
		const group = createVideoGroup();
		const a = fake(2);
		const b = fake(4);
		group.add(a);
		group.add(b);
		group.setVisible(a, false);
		expect(a.paused).toBe(true);
		expect(b.paused).toBe(false);
		group.pause();
		group.play();
		expect(a.paused).toBe(true);
		group.setVisible(a, true);
		expect(a.paused).toBe(false);
	});

	it('does not resume an off-screen clip while the group is paused', () => {
		const group = createVideoGroup();
		const a = fake(2);
		group.add(a);
		group.pause();
		group.setVisible(a, false);
		group.setVisible(a, true);
		expect(a.paused).toBe(true);
	});

	it('pulls drifting clips toward the longest clip on each tick', () => {
		const group = createVideoGroup();
		const long = fake(5);
		const short = fake(2);
		group.add(long);
		group.add(short);
		long.currentTime = 1.5;
		short.currentTime = 0.2;
		group.tick();
		expect(short.currentTime).toBe(1.5);
		long.currentTime = 4;
		group.tick();
		expect(short.currentTime).toBe(2);
	});

	it('leaves clips alone on a tick when unsynced', () => {
		const group = createVideoGroup();
		const long = fake(5);
		const short = fake(2);
		group.add(long);
		group.add(short);
		group.setSynced(false);
		long.currentTime = 3;
		short.currentTime = 0.5;
		group.tick();
		expect(short.currentTime).toBe(0.5);
	});

	it('seeks every clip to the shared time, clamped to its own length', () => {
		const group = createVideoGroup();
		const long = fake(5.2);
		const short = fake(2.4);
		group.add(long);
		group.add(short);
		group.seek(3.1);
		expect(long.currentTime).toBe(3.1);
		expect(short.currentTime).toBe(2.4);
	});

	it('restarts everything when the longest clip ends, not when a short one does', () => {
		const group = createVideoGroup();
		const long = fake(5);
		const short = fake(2);
		group.add(long);
		group.add(short);
		long.currentTime = 3;
		short.currentTime = 2;
		short.plays = 0;
		group.ended(short);
		expect(long.currentTime).toBe(3);
		group.ended(long);
		expect(long.currentTime).toBe(0);
		expect(short.currentTime).toBe(0);
		expect(short.plays).toBe(1);
	});

	it('mutes every clip, including ones added later', () => {
		const group = createVideoGroup();
		const a = fake(2, { muted: true });
		group.add(a);
		group.setMuted(false);
		expect(a.muted).toBe(false);
		const b = fake(3);
		group.add(b);
		expect(b.muted).toBe(false);
	});

	it('reports the leader clock and the longest duration', () => {
		const group = createVideoGroup();
		const long = fake(5.2);
		const short = fake(2.4);
		group.add(short);
		group.add(long);
		long.currentTime = 2.4;
		expect(group.state()).toMatchObject({ time: 2.4, duration: 5.2, synced: true, muted: true });
	});

	it('swallows a rejected play so autoplay policy never throws', async () => {
		const group = createVideoGroup();
		const rejecting = fake(2, { play: vi.fn(() => Promise.reject(new Error('NotAllowedError'))) });
		expect(() => group.add(rejecting)).not.toThrow();
		await Promise.resolve();
	});

	it('forgets a removed clip', () => {
		const group = createVideoGroup();
		const a = fake(2);
		group.add(a);
		group.remove(a);
		group.pause();
		expect(a.pauses).toBe(0);
	});
});
