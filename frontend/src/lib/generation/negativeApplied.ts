// A negative prompt is only sent to the model when the encoder runs
// classifier-free guidance — resolved guidance > 1, or NAG (nag_scale > 1)
// forcing the negative pass on even at guidance 1.0. Below that the negative is
// authored but never seen, so the form marks it inert instead of pretending it
// works. This mirrors the backend's own `do_cfg` (prompt_encoder), computed here
// from the same resolved form values the reactions engine already produced.

export type NegativeApplicability = 'applied' | 'inert' | 'unknown';

// The form field a preset binds to the encoder's guidance_scale — `cfg` across
// the whole native tree today. Deliberately NOT `guidance`: Flux names its
// distilled/embedded guidance field `guidance`, which is unrelated to CFG (Flux
// hardcodes guidance_scale = 1.0), so matching it would misread that preset. A
// preset with a differently-named CFG field overrides via `NegativeAppliedDescriptor`.
const GUIDANCE_FIELD_NAMES = ['cfg', 'cfg_scale'];
const NAG_FIELD_NAMES = ['nag_scale'];

export interface NegativeAppliedDescriptor {
	guidance_field?: string;
	nag_field?: string;
}

function pickField(
	formData: Record<string, any>,
	declared: string | undefined,
	conventional: readonly string[]
): string | undefined {
	if (declared) return declared in formData ? declared : undefined;
	return conventional.find((name) => name in formData);
}

/**
 * Whether the negative prompt is applied for the current resolved form state.
 *
 * Returns `'unknown'` (→ no notice) whenever the guidance can't be read from the
 * form: a preset with no guidance concept must never show an inert notice
 * (absence of signal ≠ inert). Fixed-guidance presets that bake the value into
 * the pipeline rather than a form field also read as `'unknown'` here — the
 * record-time backend marker still records those honestly.
 */
export function resolveNegativeApplicability(
	formData: Record<string, any> | undefined | null,
	descriptor?: NegativeAppliedDescriptor | null
): NegativeApplicability {
	if (!formData) return 'unknown';

	const guidanceField = pickField(formData, descriptor?.guidance_field, GUIDANCE_FIELD_NAMES);
	if (!guidanceField) return 'unknown';

	// An empty/unset guidance field defers to the pipeline's own server-side
	// default (Jinja `| default(...)`), which the client can't evaluate — treat
	// it as unknown rather than letting Number('') === 0 read as inert.
	const rawGuidance = formData[guidanceField];
	if (rawGuidance === '' || rawGuidance === null || rawGuidance === undefined) return 'unknown';
	const guidance = Number(rawGuidance);
	if (!Number.isFinite(guidance)) return 'unknown';

	const nagField = pickField(formData, descriptor?.nag_field, NAG_FIELD_NAMES);
	const nag = nagField != null ? Number(formData[nagField]) : NaN;
	if (Number.isFinite(nag) && nag > 1) return 'applied';

	return guidance <= 1 ? 'inert' : 'applied';
}
