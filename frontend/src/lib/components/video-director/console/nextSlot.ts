// "Add at the end / next free slot" placement for the rail's trailing `+`
// column (RailAddColumn). The add-column buttons don't know a specific
// pointer position (unlike an insert-cue click), so they need a sensible
// default time to hand the caller's `onAddBeat`/`onAddKeyframe`.
//
// Reuses the spirit of `../timelineCore.ts`'s `findSegmentGap` (largest gap,
// scanned left to right) but works over point-in-time occupancy too (a
// keyframe mark, or a shot's START/END anchors), by treating each occupied
// thing as a zero-width range.

export interface OccupiedRange {
	start: number;
	end: number;
}

/** Largest open interval's midpoint among `occupied` ranges within
 * [0, durationSeconds], snapped to 0.25s. Falls back to 0 when the shot has
 * no duration yet. */
export function nextOpenSlotSeconds(occupied: ReadonlyArray<OccupiedRange>, durationSeconds: number): number {
	if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) return 0;
	const sorted = [...occupied].sort((a, b) => a.start - b.start);
	let cursor = 0;
	let bestGapStart = 0;
	let bestGapSize = -1;
	for (const range of sorted) {
		const gap = range.start - cursor;
		if (gap > bestGapSize) {
			bestGapSize = gap;
			bestGapStart = cursor;
		}
		cursor = Math.max(cursor, range.end);
	}
	const tailGap = durationSeconds - cursor;
	if (tailGap > bestGapSize) {
		bestGapSize = tailGap;
		bestGapStart = cursor;
	}
	const midpoint = bestGapSize > 0 ? bestGapStart + bestGapSize / 2 : bestGapStart;
	const snapped = Math.round(midpoint / 0.25) * 0.25;
	return Math.min(Math.max(snapped, 0), durationSeconds);
}
