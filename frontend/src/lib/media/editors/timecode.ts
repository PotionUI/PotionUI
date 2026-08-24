/**
 * How the editors write time.
 *
 * Three shapes, because they answer different questions: a playhead reads as a
 * clock, a clip length reads as a duration, and a trim point the user is
 * dragging has to show the tenths they are dragging through or the readout
 * looks stuck.
 *
 * Every entry point takes whatever the DOM hands it - a `<video>` reports
 * `NaN` for `duration` until metadata arrives, and `Infinity` for a stream -
 * and answers with something renderable rather than "NaN:aN".
 */

/** Seconds, or 0 for anything that isn't a usable number. */
function safeSeconds(seconds: number): number {
	if (!Number.isFinite(seconds) || seconds < 0) return 0;
	return seconds;
}

function pad(value: number): string {
	return String(value).padStart(2, '0');
}

/**
 * A playhead: `m:ss`, or `h:mm:ss` past the hour. Floored, so the clock never
 * shows a second the media has not reached.
 */
export function formatTimecode(seconds: number): string {
	const total = Math.floor(safeSeconds(seconds));
	const hours = Math.floor(total / 3600);
	const minutes = Math.floor((total % 3600) / 60);
	const secs = total % 60;
	if (hours > 0) return `${hours}:${pad(minutes)}:${pad(secs)}`;
	return `${minutes}:${pad(secs)}`;
}

/**
 * A length: `8.4s` under a minute, `2:04` over it. Sub-minute clips are the
 * common case for generated video and a tenth is the difference between two
 * takes, so they keep one decimal; past a minute the tenth is noise.
 */
export function formatClipLength(seconds: number): string {
	const value = safeSeconds(seconds);
	const tenths = Math.round(value * 10) / 10;
	if (tenths < 60) {
		return `${Number.isInteger(tenths) ? tenths : tenths.toFixed(1)}s`;
	}
	// A length rounds where a playhead floors: 59.99s of clip is a minute of
	// clip, and printing "0:59" for it under-reports what the file holds.
	return formatTimecode(Math.round(value));
}

/**
 * A trim point mid-drag: `m:ss.cc`. Truncated rather than rounded so the
 * readout can never name a moment past the point that was actually set.
 */
export function formatPreciseTime(seconds: number): string {
	// Truncation happens once, in hundredths, with a tolerance: `12.34` is not
	// 12.34 in binary and `(12.34 - 12) * 100` floors to 33.
	const centiseconds = Math.floor(safeSeconds(seconds) * 100 + 1e-6);
	return `${formatTimecode(Math.floor(centiseconds / 100))}.${pad(centiseconds % 100)}`;
}
