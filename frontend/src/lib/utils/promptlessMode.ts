import { evaluateCondition, type Condition, type LogicalCondition } from '$lib/form/reactions';

/**
 * Promptless-mode gating.
 *
 * A preset may declare a `promptless_modes` var listing mode names for which the
 * prompt is irrelevant (upscale, slow-motion, LTX utility passes, …). When the
 * active mode is one of those, the generate page hides the prompt pane entirely
 * and the Generate button no longer requires prompt text. The var is authored in
 * preset.yml under `vars:` and surfaced on the loaded preset's `vars` object.
 */

export function isPromptlessMode(
	presetVars: Record<string, unknown> | null | undefined,
	mode: string | null | undefined,
	formData?: Record<string, unknown> | null
): boolean {
	if (!mode || !presetVars) return false;
	const modes = presetVars.promptless_modes;
	if (!Array.isArray(modes)) return false;
	return modes.some((entry) => {
		if (typeof entry === 'string') return entry === mode;
		if (!entry || typeof entry !== 'object' || (entry as { mode?: unknown }).mode !== mode) return false;
		const when = (entry as { when?: Condition | Condition[] | LogicalCondition }).when;
		if (when == null) return true;
		if (!formData) return false;
		try {
			return evaluateCondition(when, formData);
		} catch {
			return false;
		}
	});
}
