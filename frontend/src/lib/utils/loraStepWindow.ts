import type { LoraPickerItem } from '$lib/types/models';

/**
 * Pure helpers for a LoRA row's optional step window - the "apply this LoRA
 * only between step X and step Y" advanced control.
 *
 * Both bounds are **1-based and inclusive**, matching the backend contract
 * (`src/platform/runtime/native/lora/step_window.py`) and the way the form
 * already talks about steps (`steps: 8` means steps 1..8). A LoRA whose card
 * says "active for the first 2 of the 8 denoise steps" is therefore
 * `{step_start: 1, step_end: 2}`.
 *
 * An UNSET bound is an ABSENT key, never `null` or `0`: the backend reads a
 * missing `step_start` as 1 and a missing `step_end` as "through the last
 * step", and a row with neither key is not windowed at all and takes the
 * unchanged bake-at-load path. Writing `null` would still be read as absent,
 * but it would make every unwindowed row carry two dead keys into the session
 * blob and the generation record.
 */

export type StepBound = 'step_start' | 'step_end';

/** Smallest legal step number. Steps are 1-based, so a `0` is a user typo
 * (or an off-by-one from thinking in array indices), not "from the start". */
export const MIN_STEP = 1;

/** Reads a bound off a row. Absent, null, or non-positive reads as unset. */
export function stepBound(row: LoraPickerItem, bound: StepBound): number | null {
	const raw = row[bound];
	if (typeof raw !== 'number' || !Number.isFinite(raw) || raw < MIN_STEP) return null;
	return Math.floor(raw);
}

/** True when the row carries either bound - i.e. the sampler will toggle it
 * rather than the loader baking it in. */
export function hasStepWindow(row: LoraPickerItem): boolean {
	return stepBound(row, 'step_start') !== null || stepBound(row, 'step_end') !== null;
}

/** Short human summary for the collapsed row, e.g. `"Steps 1-2"`. Empty
 * string when the row has no window (the caller renders nothing). */
export function describeStepWindow(row: LoraPickerItem): string {
	const start = stepBound(row, 'step_start');
	const end = stepBound(row, 'step_end');
	if (start === null && end === null) return '';
	if (start !== null && end !== null) {
		return start === end ? `Step ${start}` : `Steps ${start}–${end}`;
	}
	if (start !== null) return `From step ${start}`;
	return `Through step ${end}`;
}

/** Parses a step text input. Empty/blank clears the bound (`null`);
 * anything non-numeric returns `undefined` so the caller can revert rather
 * than silently clearing a value the user meant to keep. */
export function parseStepInput(text: string): number | null | undefined {
	const trimmed = text.trim();
	if (trimmed === '') return null;
	if (!/^\d+$/.test(trimmed)) return undefined;
	const parsed = Number(trimmed);
	if (!Number.isFinite(parsed) || parsed < MIN_STEP) return undefined;
	return parsed;
}

/**
 * Returns a copy of `row` with `bound` set to `value` (or the key removed when
 * `value` is `null`), keeping `step_start <= step_end`.
 *
 * The clamp only ever moves the bound BEING EDITED: typing an end below the
 * current start raises it back to the start rather than dragging the start
 * down, so a field the user is not touching never changes under them. An
 * inverted window would be permanently-off, which is never what anyone means.
 *
 * Every other key on the row (`strength`, `saved_strength`, the other bound)
 * is carried through untouched.
 */
export function setStepBound(
	row: LoraPickerItem,
	bound: StepBound,
	value: number | null
): LoraPickerItem {
	const next: LoraPickerItem = { ...row };
	if (value === null) {
		delete next[bound];
		return next;
	}
	let clamped = Math.max(MIN_STEP, Math.floor(value));
	if (bound === 'step_end') {
		const start = stepBound(row, 'step_start');
		if (start !== null) clamped = Math.max(clamped, start);
	} else {
		const end = stepBound(row, 'step_end');
		if (end !== null) clamped = Math.min(clamped, end);
	}
	next[bound] = clamped;
	return next;
}

/** Drops both bounds - the "off" action for the whole advanced section. */
export function clearStepWindow(row: LoraPickerItem): LoraPickerItem {
	const next: LoraPickerItem = { ...row };
	delete next.step_start;
	delete next.step_end;
	return next;
}
