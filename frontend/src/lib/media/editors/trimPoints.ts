/**
 * Where a trim starts and ends.
 *
 * Trim points are held in SECONDS, not in fractions of the strip: the strip is
 * a viewport-sized element and the numbers the user is shown, the numbers the
 * API takes, and the numbers the media reports are all seconds. Fractions are
 * a rendering detail and are converted at the edge.
 *
 * The clamping mirrors `_validated_trim` in
 * `src/features/media/editing/operations.py` - start ≥ 0, end > start, start
 * inside the medium, end no further than its duration. The server checks all of
 * it again against the media's real duration; this exists so the UI cannot
 * offer a selection the server will refuse.
 */

/**
 * The shortest selection a drag may leave. Anything shorter re-encodes to a
 * file with no frames in it, which the encoder reports as a failure rather
 * than as a very short clip.
 */
export const MIN_TRIM_SECONDS = 0.05;

export interface TrimPoints {
	start: number;
	end: number;
}

export interface TrimOperationPayload {
	type: 'trim';
	start_seconds: number;
	end_seconds: number;
}

/** A duration that can be divided by, or 0 for one that cannot. */
export function safeDuration(duration: number | null | undefined): number {
	if (typeof duration !== 'number' || !Number.isFinite(duration) || duration <= 0) return 0;
	return duration;
}

/** The whole medium selected, which is what an editor opens on. */
export function fullTrim(duration: number | null | undefined): TrimPoints {
	return { start: 0, end: safeDuration(duration) };
}

function clamp(value: number, low: number, high: number): number {
	if (!Number.isFinite(value)) return low;
	return Math.min(high, Math.max(low, value));
}

/** Both points inside the medium, in order, at least `MIN_TRIM_SECONDS` apart. */
export function clampTrimPoints(points: TrimPoints, duration: number | null | undefined): TrimPoints {
	const total = safeDuration(duration);
	if (total <= 0) return { start: 0, end: 0 };
	if (total <= MIN_TRIM_SECONDS) return { start: 0, end: total };

	const start = clamp(points.start, 0, total - MIN_TRIM_SECONDS);
	const end = clamp(points.end, start + MIN_TRIM_SECONDS, total);
	return { start, end };
}

/**
 * Move the in point. The out point never moves with it - a drag that would
 * cross it stops one minimum selection short instead, so the selection cannot
 * invert while the pointer is still down.
 */
export function setTrimStart(
	points: TrimPoints,
	seconds: number,
	duration: number | null | undefined
): TrimPoints {
	const total = safeDuration(duration);
	const end = clamp(points.end, 0, total);
	return clampTrimPoints({ start: Math.min(seconds, end - MIN_TRIM_SECONDS), end }, duration);
}

/** Move the out point, under the mirror of `setTrimStart`'s rule. */
export function setTrimEnd(
	points: TrimPoints,
	seconds: number,
	duration: number | null | undefined
): TrimPoints {
	const total = safeDuration(duration);
	const start = clamp(points.start, 0, total);
	return clampTrimPoints({ start, end: Math.max(seconds, start + MIN_TRIM_SECONDS) }, duration);
}

export function trimLength(points: TrimPoints): number {
	return Math.max(0, points.end - points.start);
}

/**
 * True when the selection is the whole medium, so there is nothing to apply.
 * The tolerance is a frame's worth of slack: a strip dragged to within a
 * millisecond of the end is a user who meant the end.
 */
export function isFullClip(
	points: TrimPoints,
	duration: number | null | undefined,
	tolerance = 0.01
): boolean {
	const total = safeDuration(duration);
	if (total <= 0) return true;
	return points.start <= tolerance && points.end >= total - tolerance;
}

/** Where a point on the strip, as a 0..1 fraction of its width, lands in time. */
export function timeAtFraction(fraction: number, duration: number | null | undefined): number {
	const total = safeDuration(duration);
	return clamp(fraction, 0, 1) * total;
}

/** Where a moment in time sits on the strip, as a 0..1 fraction of its width. */
export function fractionOfTime(seconds: number, duration: number | null | undefined): number {
	const total = safeDuration(duration);
	if (total <= 0) return 0;
	return clamp(seconds / total, 0, 1);
}

/**
 * The selection as the `trim` operation the API takes.
 *
 * The end is clamped to the duration rather than sent as the user's raw drag:
 * the server allows a hair of overshoot as UI rounding but refuses a real one,
 * and there is no reason to spend that allowance.
 */
export function toTrimOperation(
	points: TrimPoints,
	duration: number | null | undefined
): TrimOperationPayload {
	const clamped = clampTrimPoints(points, duration);
	return {
		type: 'trim',
		start_seconds: Number(clamped.start.toFixed(3)),
		end_seconds: Number(clamped.end.toFixed(3))
	};
}
