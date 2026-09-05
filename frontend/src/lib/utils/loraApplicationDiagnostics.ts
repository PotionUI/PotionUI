/**
 * Pure presentation logic for a LoRA's per-adapter APPLICATION evidence (see
 * `AdapterApplication` / `emit_lora_application_diagnostics` on the backend)
 * -- distinct from the model's REQUEST (name, type, weight), which a
 * `ModelsGenerationOutput` entry always carries.
 *
 * A model entry carries diagnostics only when the backend actually computed
 * application evidence for it (a second, later `ModelsGenerationOutput` the
 * loader/generator emits once the stack has been applied); the requested-
 * stack list up front never sets these fields.
 */

export interface LoraDiagnosticModel {
	zero_effect?: boolean | null;
	unmatched_keys?: number | null;
	ignored?: string[] | null;
	unmatched_sample?: string[] | null;
}

export type LoraDiagnosticTone = 'danger' | 'warning';

export interface LoraDiagnostic {
	tone: LoraDiagnosticTone;
	/** Short badge label: "No effect" or "Partially applied". */
	label: string;
	/** One-line reason, e.g. "3 keys unmatched · dora_scale×1". */
	reason: string;
	/** Bounded sample of the unmatched key stems, for a tooltip's detail. */
	unmatchedSample: string[];
}

/**
 * `null` when `model` carries no diagnostics at all (the requested-stack
 * entry, or a fully-matched adapter with nothing ignored) -- the common
 * case, where the caller renders nothing extra.
 */
export function describeLoraDiagnostic(model: LoraDiagnosticModel): LoraDiagnostic | null {
	const unmatchedKeys = model.unmatched_keys ?? 0;
	const ignored = model.ignored ?? [];
	const zeroEffect = Boolean(model.zero_effect);

	if (!zeroEffect && unmatchedKeys <= 0 && ignored.length === 0) return null;

	const reasons: string[] = [];
	if (unmatchedKeys > 0) {
		reasons.push(`${unmatchedKeys} key${unmatchedKeys === 1 ? '' : 's'} unmatched`);
	}
	if (ignored.length > 0) {
		reasons.push(ignored.join(', '));
	}

	return {
		tone: zeroEffect ? 'danger' : 'warning',
		label: zeroEffect ? 'No effect' : 'Partially applied',
		reason: reasons.join(' · ') || 'Partial match',
		unmatchedSample: model.unmatched_sample ?? []
	};
}
