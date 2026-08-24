// Pure, Svelte-free helpers extracted from RelayTimeline.svelte so the Video
// Director timeline UI (and its tests) can share the same geometry/tick/id logic
// without importing Svelte component code.

// ─── Geometry constants (match RelayTimeline.svelte) ───────────────────────────
export const RULER_H = 26;
export const LANE_H = 46;
export const GUTTER_W = 76;
export const MIN_ZOOM = 10; // px/s
export const MAX_ZOOM = 400; // px/s
export const DEFAULT_ZOOM = 80; // px/s
export const ZOOM_STEP = 10;
export const MIN_SEG_DURATION = 0.25; // seconds
export const MIN_TIMELINE_WIDTH = 360; // px floor below which the ruler/lanes never shrink

// ─── Per-segment categorical color slot ──────────────────────────────────────────
// Same colorIndex -> --viz-N (1..8, wraps) idea as chipIndicatorColor.ts
// (lib/utils/chipIndicatorColor.ts's chip category-indicator mapping), for lanes
// that need to tell same-lane segments apart by color (e.g. multiple prompt
// windows). Returns the bare slot number so callers can compose whichever
// rgb(var(--viz-N) / alpha) string they need (solid border, 15% fill, ...);
// chipIndicatorColor itself only returns one fixed-alpha string, so it doesn't
// fit here — reuse its formula instead of importing it.
const VIZ_SLOTS = 8;
export function vizSlot(index: number): number {
	return (((index % VIZ_SLOTS) + VIZ_SLOTS) % VIZ_SLOTS) + 1;
}

// ─── Basic math ────────────────────────────────────────────────────────────────
export const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

export const sortByStart = <T extends { start: number }>(arr: T[]): T[] =>
	[...arr].sort((a, b) => a.start - b.start);

// ─── Ruler tick math ─────────────────────────────────────────────────────────────
export function getTickInterval(zoom: number): number {
	if (zoom > 200) return 0.25;
	if (zoom > 100) return 0.5;
	if (zoom > 50) return 1;
	if (zoom > 20) return 2;
	if (zoom > 10) return 5;
	return 10;
}

export function formatTick(s: number): string {
	const m = Math.floor(s / 60);
	const sec = Math.floor(s % 60);
	if (m > 0) return `${m}:${sec.toString().padStart(2, '0')}`;
	if (s < 1 && s > 0) return `${s.toFixed(2)}s`;
	return `${sec}s`;
}

export interface Tick {
	time: number;
	label: string;
}

export function buildTicks(duration: number, pxPerSec: number): Tick[] {
	const step = getTickInterval(pxPerSec);
	const out: Tick[] = [];
	for (let t = 0; t <= duration + step * 0.01; t += step) {
		const r = Math.round(t * 1000) / 1000;
		if (r <= duration) out.push({ time: r, label: formatTick(r) });
	}
	return out;
}

// ─── Monotonic id factory (no Math.random / Date.now) ───────────────────────────
export function makeIdFactory(prefix: string): () => string {
	let counter = 0;
	return () => `${prefix}-${++counter}`;
}

/** Mint `${prefix}-N` guaranteed absent from `existing` — a per-instance
 * counter (makeIdFactory) resets on remount while the DOCUMENT persists, so
 * a fresh mount over a session-restored or mode-swapped document re-mints
 * ids the document already holds and Svelte's keyed each crashes on the
 * duplicate. Always mint against the live collection instead. */
export function mintId(prefix: string, existing: ReadonlyArray<{ id: string }>): string {
	const used = new Set(existing.map((e) => e.id));
	let n = existing.length + 1;
	let id = `${prefix}-${n}`;
	while (used.has(id)) id = `${prefix}-${++n}`;
	return id;
}

// ─── Total ruler/lane width ──────────────────────────────────────────────────────
export function totalWidth(duration: number, zoom: number, min: number = MIN_TIMELINE_WIDTH): number {
	return Math.max(duration * zoom + 40, min);
}

// ─── Segment drag / trim arithmetic (px-delta → clamped seconds) ────────────────
export function dragSegment(
	origStart: number,
	origEnd: number,
	dx: number,
	zoom: number,
	duration: number
): { start: number; end: number } {
	const dur = origEnd - origStart;
	const start = clamp(origStart + dx / zoom, 0, duration - dur);
	return { start, end: start + dur };
}

export function trimSegmentLeft(
	origStart: number,
	origEnd: number,
	dx: number,
	zoom: number,
	leftBound: number
): number {
	return clamp(origStart + dx / zoom, leftBound, origEnd - MIN_SEG_DURATION);
}

export function trimSegmentRight(
	origStart: number,
	origEnd: number,
	dx: number,
	zoom: number,
	rightBound: number
): number {
	return clamp(origEnd + dx / zoom, origStart + MIN_SEG_DURATION, rightBound);
}

/** Left/right trim bounds for `id` within `sorted` (already sortByStart-ed),
 * clamped to the adjacent segment's edge or the timeline bound. */
export function neighborBounds<T extends { id: string; start: number; end: number }>(
	sorted: ReadonlyArray<T>,
	id: string,
	duration: number
): { leftBound: number; rightBound: number } {
	const idx = sorted.findIndex((s) => s.id === id);
	return {
		leftBound: idx > 0 ? sorted[idx - 1].end : 0,
		rightBound: idx >= 0 && idx < sorted.length - 1 ? sorted[idx + 1].start : duration
	};
}

/** First gap of at least `min(1, duration * 0.2)` seconds, scanning `sorted`
 * (already sortByStart-ed) left to right. `gapStart` only ever advances
 * (`Math.max`, never a bare re-assignment) — an overlapping segment further
 * along the scan must not pull the cursor backwards over an earlier one, or
 * the minted gap lands on top of it. Returns null when no room exists. */
export function findSegmentGap(
	sorted: ReadonlyArray<{ start: number; end: number }>,
	duration: number
): { start: number; end: number } | null {
	const minDur = Math.min(1, duration * 0.2);
	let gapStart = 0;
	for (const s of sorted) {
		if (s.start - gapStart >= minDur) break;
		gapStart = Math.max(gapStart, s.end);
	}
	if (gapStart >= duration - MIN_SEG_DURATION) gapStart = Math.max(0, duration - minDur);
	const gapEnd = Math.min(gapStart + minDur, duration);
	if (gapEnd - gapStart < MIN_SEG_DURATION) return null;
	return { start: gapStart, end: gapEnd };
}

// ─── Zoom stepping ────────────────────────────────────────────────────────────────
export function stepZoom(
	zoom: number,
	dir: 1 | -1,
	step: number = ZOOM_STEP,
	min: number = MIN_ZOOM,
	max: number = MAX_ZOOM
): number {
	return clamp(zoom + dir * step, min, max);
}

/** ctrl/cmd-wheel is the zoom gesture; plain wheel scrolls the timeline. */
export function isZoomGestureEvent(e: { ctrlKey: boolean; metaKey: boolean }): boolean {
	return e.ctrlKey || e.metaKey;
}

// ─── Shared window mousemove/mouseup drag-attach pattern ────────────────────────
export function attachDrag(onMove: (e: MouseEvent) => void, onUp?: () => void): void {
	const up = () => {
		window.removeEventListener('mousemove', onMove);
		window.removeEventListener('mouseup', up);
		onUp?.();
	};
	window.addEventListener('mousemove', onMove);
	window.addEventListener('mouseup', up);
}
