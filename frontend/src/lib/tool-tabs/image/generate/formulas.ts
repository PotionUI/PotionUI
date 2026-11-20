import type { AppliedSnapshot, FormulaApplication } from '$lib/formulas/types';
import { sameValue } from './tweaks';

export interface AppliedFormula {
	id: string;
	name: string;
	changed: number;
	skipped: number;
	snapshot: AppliedSnapshot;
	after: Record<string, unknown>;
}

export function appliedFormulaOf(application: FormulaApplication): AppliedFormula {
	const { applied, formData } = application;
	const fields = [...Object.keys(applied.snapshot.values), ...applied.snapshot.absent];
	const after: Record<string, unknown> = {};
	for (const field of fields) after[field] = formData[field];
	return {
		id: applied.formulaId,
		name: applied.name,
		changed: Object.keys(applied.changed).length,
		skipped: applied.skipped,
		snapshot: applied.snapshot,
		after
	};
}

export function addApplied(list: readonly AppliedFormula[], entry: AppliedFormula): AppliedFormula[] {
	return [...list.filter((item) => item.id !== entry.id), entry];
}

export function withoutApplied(values: Record<string, unknown>, entry: AppliedFormula): Record<string, unknown> {
	const next: Record<string, unknown> = { ...values };
	for (const [field, value] of Object.entries(entry.snapshot.values)) {
		if (sameValue(next[field], entry.after[field])) next[field] = value;
	}
	for (const field of entry.snapshot.absent) {
		if (sameValue(next[field], entry.after[field])) delete next[field];
	}
	return next;
}
