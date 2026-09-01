import type { LoraPickerItem } from '$lib/types/models';

/**
 * Pure strength-value logic for the `lora_picker` field, kept separate from
 * LoraPickerField.svelte so it's unit-testable without mounting a component.
 *
 * On the WIRE, `strength === 0` still means "inactive" (see `active_loras` in
 * `src/platform/templating/dict_utils.py` - it drops entries whose strength
 * is exactly zero). That backend contract is unchanged here and out of scope
 * for this fix.
 *
 * In the UI, though, "disabled" (row toggled off) and "strength is currently
 * 0" are two DIFFERENT things - `isLoraRowDisabled()` is the single source of
 * truth for the former, and it is NEVER derived from the live strength value.
 * A live strength of exactly 0 (reached by dragging the slider through zero,
 * typing 0, or nudging down to it) is a perfectly normal enabled row; only
 * the explicit toggle button disables a row. This matters because the row's
 * disabled-ness drives the HTML `disabled` attribute on the strength
 * controls (see LoraPickerField.svelte) - deriving it from `strength === 0`
 * meant a live drag THROUGH zero disabled those controls mid-gesture,
 * halting the browser's native range-input drag until the user re-clicked
 * the toggle. Negative LoRA strengths are legitimate, and crossing zero must
 * be a smooth pass-through.
 *
 * The signal `isLoraRowDisabled()` reads is the presence of `saved_strength`
 * on the row - not its value. Only `toggleLoraStrength` ever adds or removes
 * that key; a direct strength edit (drag, nudge, typed value) goes through
 * `setLoraStrength`, which carries the row's existing keys through unchanged,
 * so it can never be `strength === 0` that flips a row's disabled-ness - only
 * the toggle can. These helpers COPY the rest of the row rather than rebuilding
 * it from `{model, strength}`, because a row now carries settings that are
 * nothing to do with strength (`step_start`/`step_end` - see loraStepWindow.ts)
 * and moving a slider must not silently discard them.
 *
 * The remembered strength lives ON THE ROW ITSELF (`saved_strength`), not in
 * component state, so it survives a page reload - it round-trips through the
 * Session feature's raw `data` JSON blob (src/features/sessions/manager.py -
 * no per-field schema on save/load) the same way the rest of a tab's form
 * data does. This is deliberately safe to ride along on the wire value: a
 * preset's pipeline.yml reads the keys it wants off each row explicitly (never
 * a wholesale dict copy or `tojson`), so a key no template mentions is
 * invisible downstream, both in the pipe config and in the stored generation
 * record. `saved_strength` is such a key. `step_start`/`step_end` are NOT -
 * they are read by `LoraPicker.input()` (only when the field declares
 * `allow_step_window`) and forwarded by the Krea-2 pipelines into
 * `model_loader/krea2`, which is the whole point of them.
 *
 * Residual note (not fixed here, backend-visible): a row parked at exactly
 * live strength 0 - enabled, mid-drag or deliberately - is still, on the
 * wire, indistinguishable from "inactive" to `active_loras()`, which drops
 * any entry whose strength is exactly zero regardless of this row's UI
 * disabled-ness. Functionally that's a no-op (a LoRA applied at weight 0
 * contributes nothing either way), so it doesn't change generation output,
 * but it means the backend has no concept of "explicitly enabled, resting at
 * zero" - only the UI does. Giving the backend that concept too (e.g. an
 * explicit enabled flag it respects) would be a separate, deliberate
 * contract change, not a side effect of this fix.
 */

export function clampStrength(value: number, min: number, max: number): number {
	if (Number.isNaN(value)) return value;
	return Math.min(max, Math.max(min, value));
}

/** Parses a typed strength value, clamped to [min, max] but never rounded to
 * a step - arbitrary precision (e.g. 0.04 on a 0.1-step slider) must survive. */
export function parseStrengthInput(text: string, min: number, max: number): number | null {
	const parsed = parseFloat(text);
	if (Number.isNaN(parsed)) return null;
	return clampStrength(parsed, min, max);
}

const NUDGE_STEP = 0.05;
const NUDGE_STEP_LARGE = 0.25;
// Rounds nudge results to 3 decimals so repeated +0.05 nudges don't drift into
// float noise (0.1 + 0.05 = 0.15000000000000002); unrelated to display precision.
const NUDGE_ROUND = 1000;

export interface NudgeOptions {
	large: boolean;
	min: number;
	max: number;
	step?: number;
	largeStep?: number;
}

export function nudgeStrength(current: number, direction: 1 | -1, options: NudgeOptions): number {
	const amount = options.large ? (options.largeStep ?? NUDGE_STEP_LARGE) : (options.step ?? NUDGE_STEP);
	const next = Math.round((current + direction * amount) * NUDGE_ROUND) / NUDGE_ROUND;
	return clampStrength(next, options.min, options.max);
}

/** Two decimals for the common case, but a value carrying more precision
 * (typed 0.125, 0.004) is shown exactly - the display must never contradict
 * the wire value parseStrengthInput preserved. */
export function formatStrength(value: number): string {
	const fixed = value.toFixed(2);
	return Number(fixed) === value ? fixed : String(value);
}

/** The single source of truth for a row's toggled-off state - see the module
 * doc comment above for why this must never be derived from the live
 * `strength` value. Only `toggleLoraStrength`'s off-branch ever attaches
 * `saved_strength`; a live strength of exactly 0 with no `saved_strength` is
 * an enabled row resting at zero, not a disabled one. */
export function isLoraRowDisabled(row: LoraPickerItem): boolean {
	return row.saved_strength !== undefined && row.saved_strength !== null;
}

/** Turning off zeroes `strength` and remembers the prior (possibly already-
 * zero) value in `saved_strength` - the presence of that key, not the
 * strength value, is what makes the row disabled from here on. Turning on
 * restores `saved_strength` (dropping it from the returned row - it's only
 * meaningful while off), falling back to `defaultStrength` (or 1 if even
 * that is zero) when the remembered value was itself zero (nothing
 * meaningful to restore to). */
export function toggleLoraStrength(row: LoraPickerItem, defaultStrength: number): LoraPickerItem {
	if (!isLoraRowDisabled(row)) {
		return { ...row, strength: 0, saved_strength: row.strength };
	}

	const restored =
		row.saved_strength && row.saved_strength !== 0
			? row.saved_strength
			: defaultStrength !== 0
				? defaultStrength
				: 1;

	const next: LoraPickerItem = { ...row, strength: restored };
	delete next.saved_strength;
	return next;
}

/** A direct strength edit (drag, nudge, typed value). Spreads rather than
 * rebuilding so a row's OTHER settings - its step window above all - survive
 * moving the slider; `saved_strength` is deliberately included in that spread,
 * so this can never flip a row's disabled-ness in either direction. */
export function setLoraStrength(row: LoraPickerItem, strength: number): LoraPickerItem {
	return { ...row, strength };
}
