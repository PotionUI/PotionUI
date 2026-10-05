import { evaluateCondition, type Condition, type LogicalCondition } from '$lib/form/reactions';

export type NegativeAppliesWhen = boolean | Condition | Condition[] | LogicalCondition;

export interface NegativePromptModeDeclaration {
	default?: NegativeAppliesWhen | null;
	variants?: Record<string, NegativeAppliesWhen | null>;
}

export interface NegativePromptDeclarations {
	applies_when?: NegativeAppliesWhen | null;
	modes?: Record<string, NegativePromptModeDeclaration>;
}

export function resolveNegativeAppliesWhen(
	declarations: NegativePromptDeclarations | null | undefined,
	mode: string | null | undefined,
	variant?: string | null
): NegativeAppliesWhen | null {
	if (!declarations) return null;
	const modeEntry = mode ? declarations.modes?.[mode] : undefined;
	if (modeEntry) {
		if (variant && modeEntry.variants && variant in modeEntry.variants) {
			return modeEntry.variants[variant] ?? null;
		}
		return modeEntry.default ?? null;
	}
	return declarations.applies_when ?? null;
}

export function isNegativeInert(
	declarations: NegativePromptDeclarations | null | undefined,
	mode: string | null | undefined,
	variant: string | null | undefined,
	formData: Record<string, any> | null | undefined
): boolean {
	const when = resolveNegativeAppliesWhen(declarations, mode, variant);
	if (when === null || when === undefined) return false;
	if (typeof when === 'boolean') return !when;
	if (!formData) return false;
	return !evaluateCondition(when, formData);
}
