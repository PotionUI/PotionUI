/**
 * Promptless-mode gating.
 *
 * A preset may declare a `promptless_modes` var listing mode names for which the
 * prompt is irrelevant (upscale, slow-motion, LTX utility passes, …). When the
 * active mode is one of those, the generate page hides the prompt pane entirely
 * and the Generate button no longer requires prompt text. The var is authored in
 * preset.yml under `vars:` and surfaced on the loaded preset's `vars` object.
 */

/**
 * True when `mode` is listed in the preset's `promptless_modes` var.
 *
 * Tolerates the many shapes preset vars arrive in: a missing var, a non-array
 * value, or a null/empty mode all resolve to `false` (i.e. prompt required).
 */
export function isPromptlessMode(
	presetVars: Record<string, unknown> | null | undefined,
	mode: string | null | undefined
): boolean {
	if (!mode || !presetVars) return false;
	const modes = presetVars.promptless_modes;
	if (!Array.isArray(modes)) return false;
	return modes.includes(mode);
}
