/**
 * Pure math for the Studio dock's progress ring and HUD timer — kept
 * side-effect free so both can be unit tested without mounting a component.
 */

/** `mm:ss` elapsed-time readout for the generating-state status pill. Unlike
 *  `formatDurationSeconds` (generation-panel/barState.ts), which renders a
 *  loosely-formatted "Xm Ys"/"Xh Ym" readout cell, the camera-HUD pill wants
 *  a fixed-width clock face — `00:07`, not `7s`. */
export function formatElapsedClock(ms: number | null): string {
	if (ms === null || !Number.isFinite(ms) || ms < 0) return '00:00';
	const totalSeconds = Math.floor(ms / 1000);
	const minutes = Math.floor(totalSeconds / 60);
	const seconds = totalSeconds % 60;
	return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

/** Circle circumference for an SVG progress ring of the given radius. */
export function ringCircumference(radius: number): number {
	return 2 * Math.PI * radius;
}

/**
 * `stroke-dashoffset` for a determinate ring — `null` when `progress` itself
 * is `null` (no fraction reported yet), so the caller can fall back to an
 * indeterminate spin instead of drawing a ring frozen at 0%.
 */
export function ringDashOffset(progress: number | null, circumference: number): number | null {
	if (progress === null || !Number.isFinite(progress)) return null;
	const clamped = Math.min(1, Math.max(0, progress));
	return circumference * (1 - clamped);
}
