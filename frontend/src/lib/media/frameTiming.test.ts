import { describe, it, expect } from 'vitest';
import {
	ASSUMED_FPS,
	clampFrameTime,
	formatFramePosition,
	frameCount,
	frameDuration,
	frameIndexAt,
	lastFrameTime,
	safeFps,
	snapToFrame,
	timeOfFrame
} from './frameTiming';

describe('safeFps', () => {
	it('falls back for a video whose fps never resolved', () => {
		expect(safeFps(null)).toBe(ASSUMED_FPS);
		expect(safeFps(0)).toBe(ASSUMED_FPS);
		expect(safeFps(NaN)).toBe(ASSUMED_FPS);
		expect(safeFps(-30)).toBe(ASSUMED_FPS);
	});

	it('keeps a real one', () => {
		expect(safeFps(23.976)).toBe(23.976);
	});
});

describe('frameIndexAt', () => {
	it('counts from zero', () => {
		expect(frameIndexAt(0, 24)).toBe(0);
		expect(frameIndexAt(1, 24)).toBe(24);
	});

	it('stays on the frame that is actually on screen', () => {
		expect(frameIndexAt(0.99 / 24, 24)).toBe(0);
		expect(frameIndexAt(1.01 / 24, 24)).toBe(1);
	});

	it('does not lose a frame to binary rounding at an exact boundary', () => {
		expect(frameIndexAt(3 / 24, 24)).toBe(3);
		expect(frameIndexAt(7 / 30, 30)).toBe(7);
	});

	it('clamps a negative time', () => {
		expect(frameIndexAt(-2, 24)).toBe(0);
	});
});

describe('timeOfFrame / snapToFrame', () => {
	it('round-trips a frame index', () => {
		expect(frameIndexAt(timeOfFrame(17, 30), 30)).toBe(17);
	});

	it('snaps back to the start of the frame it lands in', () => {
		expect(snapToFrame(0.7, 10)).toBeCloseTo(0.7);
		expect(snapToFrame(0.75, 10)).toBeCloseTo(0.7);
	});
});

describe('frameCount', () => {
	it('counts the frames a clip holds', () => {
		expect(frameCount(8.4, 25)).toBe(210);
	});

	it('is zero for a clip with no duration', () => {
		expect(frameCount(null, 25)).toBe(0);
		expect(frameCount(0, 25)).toBe(0);
	});
});

describe('lastFrameTime', () => {
	it('is one frame short of the duration, not the duration', () => {
		// `extract_video_frame` refuses `time_seconds >= duration`.
		expect(lastFrameTime(8, 25)).toBeCloseTo(8 - 1 / 25);
		expect(lastFrameTime(8, 25)).toBeLessThan(8);
	});

	it('is zero for a clip with no duration', () => {
		expect(lastFrameTime(null, 25)).toBe(0);
	});
});

describe('clampFrameTime', () => {
	it('keeps a time the endpoint would accept', () => {
		const time = clampFrameTime(4, 8, 25);
		expect(time).toBeGreaterThanOrEqual(0);
		expect(time).toBeLessThan(8);
	});

	it('refuses the duration itself, which the server treats as past the end', () => {
		expect(clampFrameTime(8, 8, 25)).toBeLessThan(8);
		expect(clampFrameTime(99, 8, 25)).toBeLessThan(8);
	});

	it('lands on a frame boundary', () => {
		expect(clampFrameTime(1.03, 8, 10)).toBeCloseTo(1.0);
	});

	it('clamps below zero', () => {
		expect(clampFrameTime(-5, 8, 25)).toBe(0);
	});
});

describe('frameDuration', () => {
	it('is the reciprocal of the rate', () => {
		expect(frameDuration(50)).toBeCloseTo(0.02);
	});
});

describe('formatFramePosition', () => {
	it('reads as a one-based position out of the total', () => {
		expect(formatFramePosition(0, 8, 25)).toBe('1 / 200');
		expect(formatFramePosition(4, 8, 25)).toBe('101 / 200');
	});
});
