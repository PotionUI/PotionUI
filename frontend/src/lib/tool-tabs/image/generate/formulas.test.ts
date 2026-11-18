import { describe, expect, it } from 'vitest';
import type { FormulaApplication } from '$lib/formulas/types';
import { addApplied, appliedFormulaOf, withoutApplied } from './formulas';

function application(id: string, before: Record<string, unknown>, after: Record<string, unknown>, skipped = 0): FormulaApplication {
	const values: Record<string, unknown> = {};
	const absent: string[] = [];
	const changed: FormulaApplication['applied']['changed'] = {};
	for (const field of Object.keys(after)) {
		if (field in before) values[field] = before[field];
		else absent.push(field);
		changed[field] = { old: before[field], new: after[field], label: field };
	}
	return { formData: { ...before, ...after }, applied: { formulaId: id, name: `Formula ${id}`, key: 'k', changed, skipped, snapshot: { values, absent } } };
}

describe('applied formulas in the editor', () => {
	it('counts what a formula changed and skipped', () => {
		const entry = appliedFormulaOf(application('a', { steps: 28 }, { steps: 8, cfg: 1 }, 2));
		expect(entry).toMatchObject({ id: 'a', name: 'Formula a', changed: 2, skipped: 2 });
		expect(entry.after).toEqual({ steps: 8, cfg: 1 });
	});

	it('removes a formula by putting back what it replaced', () => {
		const entry = appliedFormulaOf(application('a', { steps: 28 }, { steps: 8, cfg: 1 }));
		expect(withoutApplied({ steps: 8, cfg: 1, seed: 3 }, entry)).toEqual({ steps: 28, seed: 3 });
	});

	it('leaves alone a field the member or a later formula changed since', () => {
		const first = appliedFormulaOf(application('a', { steps: 28 }, { steps: 8 }));
		expect(withoutApplied({ steps: 12 }, first)).toEqual({ steps: 12 });
	});

	it('keeps one entry per formula, the latest application last', () => {
		const first = appliedFormulaOf(application('a', {}, { steps: 8 }));
		const second = appliedFormulaOf(application('b', {}, { cfg: 2 }));
		const again = appliedFormulaOf(application('a', {}, { steps: 6 }));
		expect(addApplied(addApplied([], first), second).map((entry) => entry.id)).toEqual(['a', 'b']);
		expect(addApplied([first, second], again).map((entry) => entry.id)).toEqual(['b', 'a']);
	});
});
