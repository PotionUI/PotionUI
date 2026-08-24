import { describe, it, expect } from 'vitest';
import {
	clamp,
	sortByStart,
	getTickInterval,
	formatTick,
	buildTicks,
	makeIdFactory,
	mintId,
	vizSlot,
	totalWidth,
	dragSegment,
	trimSegmentLeft,
	trimSegmentRight,
	neighborBounds,
	findSegmentGap,
	stepZoom,
	isZoomGestureEvent,
	MIN_ZOOM,
	MAX_ZOOM,
	DEFAULT_ZOOM,
	MIN_SEG_DURATION,
	MIN_TIMELINE_WIDTH,
	ZOOM_STEP,
	RULER_H,
	LANE_H,
	GUTTER_W
} from './timelineCore';

describe('geometry constants', () => {
	it('match RelayTimeline.svelte values', () => {
		expect(RULER_H).toBe(26);
		expect(LANE_H).toBe(46);
		expect(GUTTER_W).toBe(76);
		expect(MIN_ZOOM).toBe(10);
		expect(MAX_ZOOM).toBe(400);
		expect(DEFAULT_ZOOM).toBe(80);
		expect(MIN_SEG_DURATION).toBe(0.25);
	});
});

describe('clamp', () => {
	it('clamps within bounds', () => {
		expect(clamp(5, 0, 10)).toBe(5);
		expect(clamp(-5, 0, 10)).toBe(0);
		expect(clamp(15, 0, 10)).toBe(10);
	});
});

describe('sortByStart', () => {
	it('sorts by start ascending without mutating input', () => {
		const arr = [{ start: 3 }, { start: 1 }, { start: 2 }];
		const sorted = sortByStart(arr);
		expect(sorted.map((s) => s.start)).toEqual([1, 2, 3]);
		expect(arr.map((s) => s.start)).toEqual([3, 1, 2]);
	});
});

describe('getTickInterval', () => {
	it('matches RelayTimeline breakpoints', () => {
		expect(getTickInterval(250)).toBe(0.25);
		expect(getTickInterval(201)).toBe(0.25);
		expect(getTickInterval(200)).toBe(0.5);
		expect(getTickInterval(150)).toBe(0.5);
		expect(getTickInterval(101)).toBe(0.5);
		expect(getTickInterval(100)).toBe(1);
		expect(getTickInterval(51)).toBe(1);
		expect(getTickInterval(50)).toBe(2);
		expect(getTickInterval(21)).toBe(2);
		expect(getTickInterval(20)).toBe(5);
		expect(getTickInterval(11)).toBe(5);
		expect(getTickInterval(10)).toBe(10);
		expect(getTickInterval(1)).toBe(10);
	});
});

describe('formatTick', () => {
	it('formats sub-second values with two decimals', () => {
		expect(formatTick(0.5)).toBe('0.50s');
	});
	it('formats whole seconds under a minute', () => {
		expect(formatTick(5)).toBe('5s');
		expect(formatTick(0)).toBe('0s');
	});
	it('formats minutes:seconds once past 60s', () => {
		expect(formatTick(65)).toBe('1:05');
		expect(formatTick(600)).toBe('10:00');
	});
});

describe('buildTicks', () => {
	it('produces ticks up to and including duration at the given zoom', () => {
		const ticks = buildTicks(3, 150); // zoom=150 -> interval 0.5
		expect(ticks.map((t) => t.time)).toEqual([0, 0.5, 1, 1.5, 2, 2.5, 3]);
		expect(ticks[0].label).toBe('0s');
		expect(ticks[1].label).toBe('0.50s');
	});

	it('never exceeds duration', () => {
		const ticks = buildTicks(2.2, 150); // step 0.5 -> 0,0.5,1,1.5,2 (2.5 excluded)
		expect(ticks[ticks.length - 1].time).toBeLessThanOrEqual(2.2);
		expect(ticks.map((t) => t.time)).toEqual([0, 0.5, 1, 1.5, 2]);
	});

	it('returns a single zero tick for zero duration', () => {
		expect(buildTicks(0, 80).map((t) => t.time)).toEqual([0]);
	});
});

describe('makeIdFactory', () => {
	it('produces monotonic prefixed ids starting at 1', () => {
		const makeId = makeIdFactory('seg');
		expect(makeId()).toBe('seg-1');
		expect(makeId()).toBe('seg-2');
		expect(makeId()).toBe('seg-3');
	});

	it('keeps independent counters per factory instance', () => {
		const a = makeIdFactory('a');
		const b = makeIdFactory('b');
		expect(a()).toBe('a-1');
		expect(b()).toBe('b-1');
		expect(a()).toBe('a-2');
	});
});

// mintId guards the crash documented at timelineCore.ts:60-64: a per-instance
// counter (makeIdFactory) resets on remount while the document persists, so a
// fresh mount over a session-restored/mode-swapped document can re-mint an id
// the document already holds — Svelte's keyed {#each} throws on the duplicate.
describe('mintId', () => {
	it('mints the next id past the existing collection when uncontested', () => {
		const existing = [{ id: 'seg-1' }, { id: 'seg-2' }];
		expect(mintId('seg', existing)).toBe('seg-3');
	});

	it('mints seg-1 against an empty collection', () => {
		expect(mintId('seg', [])).toBe('seg-1');
	});

	it('skips past a collision instead of returning a duplicate', () => {
		// existing.length + 1 would collide with seg-3, which the document
		// already holds — this is exactly the remount scenario from the
		// docblock: a fresh per-instance counter reproposes an id a
		// session-restored document still has.
		const existing = [{ id: 'seg-1' }, { id: 'seg-2' }, { id: 'seg-3' }];
		const minted = mintId('seg', existing);
		expect(minted).not.toBe('seg-3');
		expect(existing.some((e) => e.id === minted)).toBe(false);
	});

	it('skips a whole run of collisions', () => {
		const existing = [{ id: 'seg-1' }, { id: 'seg-2' }, { id: 'seg-3' }, { id: 'seg-4' }, { id: 'seg-5' }];
		expect(mintId('seg', existing)).toBe('seg-6');
	});
});

describe('vizSlot', () => {
	it('starts at 1 and wraps at 8 (same slot idea as chipIndicatorColor)', () => {
		expect(vizSlot(0)).toBe(1);
		expect(vizSlot(7)).toBe(8);
		expect(vizSlot(8)).toBe(1);
		expect(vizSlot(15)).toBe(8);
	});

	it('wraps negative indices into range', () => {
		expect(vizSlot(-1)).toBe(8);
	});
});

describe('totalWidth', () => {
	it('floors at MIN_TIMELINE_WIDTH (360) for a short/zoomed-out timeline', () => {
		expect(MIN_TIMELINE_WIDTH).toBe(360);
		expect(totalWidth(0, 80)).toBe(360);
		expect(totalWidth(2, 80)).toBe(360);
	});

	it('grows past the floor once duration*zoom + 40 exceeds it', () => {
		expect(totalWidth(10, 80)).toBe(840);
	});
});

describe('dragSegment', () => {
	it('moves start/end by dx/zoom, preserving duration', () => {
		expect(dragSegment(2, 5, 80, 80, 20)).toEqual({ start: 3, end: 6 });
	});

	it('clamps at 0 without changing duration', () => {
		expect(dragSegment(2, 5, -1000, 80, 20)).toEqual({ start: 0, end: 3 });
	});

	it('clamps at the timeline end without changing duration', () => {
		expect(dragSegment(2, 5, 2000, 80, 20)).toEqual({ start: 17, end: 20 });
	});
});

describe('trimSegmentLeft / trimSegmentRight', () => {
	it('trimSegmentLeft moves start toward the pointer, bounded by leftBound and MIN_SEG_DURATION', () => {
		expect(trimSegmentLeft(2, 5, 80, 80, 0)).toBe(3);
		expect(trimSegmentLeft(2, 5, -1000, 80, 1)).toBe(1); // clamped at neighbor's end
		expect(trimSegmentLeft(2, 5, 1000, 80, 0)).toBe(5 - MIN_SEG_DURATION); // never crosses origEnd
	});

	it('trimSegmentRight moves end toward the pointer, bounded by rightBound and MIN_SEG_DURATION', () => {
		expect(trimSegmentRight(2, 5, 80, 80, 10)).toBe(6);
		expect(trimSegmentRight(2, 5, 1000, 80, 8)).toBe(8); // clamped at neighbor's start
		expect(trimSegmentRight(2, 5, -1000, 80, 10)).toBe(2 + MIN_SEG_DURATION); // never crosses origStart
	});
});

describe('neighborBounds', () => {
	const sorted = [
		{ id: 'a', start: 0, end: 2 },
		{ id: 'b', start: 3, end: 5 },
		{ id: 'c', start: 7, end: 9 }
	];

	it('bounds a middle segment by its neighbors', () => {
		expect(neighborBounds(sorted, 'b', 10)).toEqual({ leftBound: 2, rightBound: 7 });
	});

	it('bounds the first segment by 0 on the left', () => {
		expect(neighborBounds(sorted, 'a', 10)).toEqual({ leftBound: 0, rightBound: 3 });
	});

	it('bounds the last segment by duration on the right', () => {
		expect(neighborBounds(sorted, 'c', 10)).toEqual({ leftBound: 5, rightBound: 10 });
	});
});

// findSegmentGap: divergence #2 from an earlier audit. RelayTimeline advanced
// `gapStart = Math.max(gapStart, s.end)`; PromptTimelineField did
// `gapStart = s.end`, which can move the cursor BACKWARDS across an
// overlapping segment and place the new range on top of an earlier one. This
// suite pins the correct (Relay) behavior — both files are migrated onto it.
describe('findSegmentGap', () => {
	it('places a new segment right after the sole existing one', () => {
		const sorted = [{ start: 0, end: 2 }];
		expect(findSegmentGap(sorted, 10)).toEqual({ start: 2, end: 3 });
	});

	it('returns null when the computed gap is thinner than MIN_SEG_DURATION', () => {
		// duration 0.4s -> minDur = min(1, 0.4*0.2) = 0.08s, below MIN_SEG_DURATION (0.25s).
		expect(findSegmentGap([], 0.4)).toBeNull();
	});

	it('never regresses gapStart across a later, earlier-ending overlapping segment (the fixed bug)', () => {
		// A: 0–5, B: 2–3 (nested inside A, still sorted by start since 0 < 2).
		// The naive `gapStart = s.end` walk visits A (gapStart -> 5) then B
		// (gapStart -> 3, backwards) and mints a gap at [3, 4) — on top of A.
		// The Math.max walk holds gapStart at 5 and mints [5, 6) instead.
		const sorted = [
			{ start: 0, end: 5 },
			{ start: 2, end: 3 }
		];
		const gap = findSegmentGap(sorted, 10);
		expect(gap).toEqual({ start: 5, end: 6 });
		// Explicitly assert the fix: the gap does not overlap segment A.
		expect(gap!.start).toBeGreaterThanOrEqual(5);
	});
});

describe('stepZoom', () => {
	it('steps up by ZOOM_STEP, clamped at MAX_ZOOM', () => {
		expect(stepZoom(80, 1)).toBe(80 + ZOOM_STEP);
		expect(stepZoom(MAX_ZOOM, 1)).toBe(MAX_ZOOM);
	});

	it('steps down by ZOOM_STEP, clamped at MIN_ZOOM', () => {
		expect(stepZoom(80, -1)).toBe(80 - ZOOM_STEP);
		expect(stepZoom(MIN_ZOOM, -1)).toBe(MIN_ZOOM);
	});
});

describe('isZoomGestureEvent', () => {
	it('is true for ctrl or meta wheel, false for a plain wheel', () => {
		expect(isZoomGestureEvent({ ctrlKey: true, metaKey: false })).toBe(true);
		expect(isZoomGestureEvent({ ctrlKey: false, metaKey: true })).toBe(true);
		expect(isZoomGestureEvent({ ctrlKey: false, metaKey: false })).toBe(false);
	});
});
