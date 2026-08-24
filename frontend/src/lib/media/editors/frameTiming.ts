/**
 * Frames ↔ time, for the editor that lifts one still out of a video.
 *
 * A `<video>` element seeks in seconds and knows nothing about frames, but the
 * thing a user picks is a frame - so the two conversions live here, where the
 * off-by-one that turns "the last frame" into "past the end" can be asserted.
 *
 * `extract_video_frame` (src/features/media/editing/operations.py) refuses a
 * time at or past the duration outright, which makes the final frame the one
 * that is easiest to ask for and easiest to get wrong: it starts at
 * `duration - 1/fps`, not at `duration`.
 */

/** Fallback step for a video whose fps never resolved. */
export const ASSUMED_FPS = 24;

export function safeFps(fps: number | null | undefined): number {
	if (typeof fps !== 'number' || !Number.isFinite(fps) || fps <= 0) return ASSUMED_FPS;
	return fps;
}

/** How long one frame lasts. */
export function frameDuration(fps: number | null | undefined): number {
	return 1 / safeFps(fps);
}

/** Which frame is on screen at `seconds`, counting from 0. */
export function frameIndexAt(seconds: number, fps: number | null | undefined): number {
	if (!Number.isFinite(seconds) || seconds <= 0) return 0;
	return Math.floor(seconds * safeFps(fps) + 1e-6);
}

/** When frame `index` starts. */
export function timeOfFrame(index: number, fps: number | null | undefined): number {
	if (!Number.isFinite(index) || index <= 0) return 0;
	return Math.floor(index) / safeFps(fps);
}

/** The start of whichever frame `seconds` falls inside. */
export function snapToFrame(seconds: number, fps: number | null | undefined): number {
	return timeOfFrame(frameIndexAt(seconds, fps), fps);
}

/** How many whole frames a clip holds. */
export function frameCount(duration: number | null | undefined, fps: number | null | undefined): number {
	if (typeof duration !== 'number' || !Number.isFinite(duration) || duration <= 0) return 0;
	return Math.max(1, Math.round(duration * safeFps(fps)));
}

/** When the last extractable frame starts - `duration` itself is past the end. */
export function lastFrameTime(
	duration: number | null | undefined,
	fps: number | null | undefined
): number {
	if (typeof duration !== 'number' || !Number.isFinite(duration) || duration <= 0) return 0;
	return Math.max(0, snapToFrame(Math.max(0, duration - frameDuration(fps)), fps));
}

/**
 * A time the frame endpoint will accept: on a frame boundary, inside the clip,
 * and never equal to the duration.
 */
export function clampFrameTime(
	seconds: number,
	duration: number | null | undefined,
	fps: number | null | undefined
): number {
	const last = lastFrameTime(duration, fps);
	if (!Number.isFinite(seconds) || seconds <= 0) return 0;
	return Math.min(snapToFrame(seconds, fps), last);
}

/** `frame 42 / 202` - the position, in the unit the user is picking in. */
export function formatFramePosition(
	seconds: number,
	duration: number | null | undefined,
	fps: number | null | undefined
): string {
	return `${frameIndexAt(seconds, fps) + 1} / ${frameCount(duration, fps)}`;
}
