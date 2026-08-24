/**
 * The arithmetic behind "split into parts".
 *
 * The input is a part LENGTH in seconds, not a part count — the user does not
 * know how many parts a clip should become, only how long each one should be.
 * A remainder shorter than the part length is kept as a final short clip
 * rather than dropped or folded into the last full part: 65s at 10s is 7
 * parts (6 × 10s + one 5s), never 6 parts of ~10.8s each.
 *
 * Pure so the preview line and the disabled-reason logic can both read it
 * without touching the DOM, and so it can be unit tested directly.
 */

import { formatClipLength } from './timecode';

/** The shortest part length worth sending - anything shorter is almost
 *  certainly a typo mid-entry, and a part under a tenth of a second re-encodes
 *  to a file with effectively no frames in it. */
export const MIN_PART_SECONDS = 0.1;

/** Floating-point slack for "the remainder divides the clip evenly". */
const REMAINDER_TOLERANCE = 0.005;

export interface SplitPlan {
	/** How many full-length parts the clip yields. */
	wholeParts: number;
	/** The final short clip's length, or 0 when the clip divides evenly. */
	remainderSeconds: number;
	/** `wholeParts`, plus one more when there is a remainder. */
	totalParts: number;
}

/**
 * Why `partSeconds` cannot be split into, or null when it can.
 *
 * `partSeconds` must be positive and strictly less than the clip - a length
 * that meets or exceeds the whole clip would produce exactly one "part",
 * which is not a split at all.
 */
export function describeSplitRejection(
	partSeconds: number,
	duration: number | null | undefined
): string | null {
	const total = typeof duration === 'number' && Number.isFinite(duration) ? duration : 0;
	if (total <= 0) return 'Waiting for the clip to report its length…';
	if (!Number.isFinite(partSeconds) || partSeconds <= 0) {
		return 'Enter a part length greater than zero.';
	}
	if (partSeconds < MIN_PART_SECONDS) {
		return `Parts must be at least ${MIN_PART_SECONDS}s long.`;
	}
	if (partSeconds >= total) {
		return 'Part length must be shorter than the clip - otherwise there is nothing to split.';
	}
	return null;
}

/** The parts a clip of `duration` seconds splits into at `partSeconds` each, or null when invalid. */
export function computeSplitPlan(
	partSeconds: number,
	duration: number | null | undefined
): SplitPlan | null {
	if (describeSplitRejection(partSeconds, duration) !== null) return null;
	const total = duration as number;

	const wholeParts = Math.floor(total / partSeconds);
	const rawRemainder = total - wholeParts * partSeconds;
	const remainderSeconds = rawRemainder <= REMAINDER_TOLERANCE ? 0 : rawRemainder;

	return {
		wholeParts,
		remainderSeconds,
		totalParts: remainderSeconds > 0 ? wholeParts + 1 : wholeParts
	};
}

/** The live preview line, e.g. `65s → 7 parts (6 × 10s + 5s)`. */
export function describeSplitPlan(partSeconds: number, duration: number | null | undefined): string {
	const plan = computeSplitPlan(partSeconds, duration);
	if (!plan) return '';

	const breakdown =
		plan.remainderSeconds > 0
			? `${plan.wholeParts} × ${formatClipLength(partSeconds)} + ${formatClipLength(plan.remainderSeconds)}`
			: `${plan.wholeParts} × ${formatClipLength(partSeconds)}`;

	return `${formatClipLength(duration as number)} → ${plan.totalParts} part${plan.totalParts === 1 ? '' : 's'} (${breakdown})`;
}

/**
 * The part length as the API takes it - rounded the same way `toTrimOperation`
 * rounds a trim point, so floating-point drift from typing never reaches the
 * server as noise past the millisecond.
 */
export function toSplitPayload(partSeconds: number): number {
	return Number(partSeconds.toFixed(3));
}
