import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

const listFormulas = vi.hoisted(() => vi.fn());

vi.mock('$lib/services/api/index', () => ({ api: { listFormulas } }));

import { formulaLists, formulasKey, loadFormulas, removeFormula, upsertFormula } from './formulas';
import type { Formula } from '$lib/formulas/types';

function formula(id: string, extra: Partial<Formula> = {}): Formula {
	return { id, preset_id: 'p', mode: 'm', name: id, ...extra } as Formula;
}

function listOf(presetId = 'p', mode = 'm') {
	return get(formulaLists)[formulasKey(presetId, mode)];
}

describe('formulas store', () => {
	beforeEach(() => {
		listFormulas.mockReset();
	});

	it('keys lists by preset and mode', () => {
		expect(formulasKey('p', 'm')).not.toBe(formulasKey('p', 'n'));
		expect(formulasKey('p', 'm')).not.toBe(formulasKey('q', 'm'));
	});

	it('loads a list and marks it loaded', async () => {
		listFormulas.mockResolvedValue({ success: true, data: [formula('a'), formula('b')] });
		await loadFormulas('load', 'ok');
		expect(listFormulas).toHaveBeenCalledWith('load', 'ok');
		expect(listOf('load', 'ok')).toMatchObject({ loading: false, loaded: true, error: null });
		expect(listOf('load', 'ok').items.map((f) => f.id)).toEqual(['a', 'b']);
	});

	it('records the server error and stays unloaded', async () => {
		listFormulas.mockResolvedValue({ success: false, error: 'nope' });
		await loadFormulas('load', 'fail');
		expect(listOf('load', 'fail')).toMatchObject({ loading: false, loaded: false, error: 'nope', items: [] });
	});

	it('records a rejected request and clears the error on the next success', async () => {
		listFormulas.mockRejectedValueOnce(new Error('offline'));
		await loadFormulas('load', 'retry');
		expect(listOf('load', 'retry').error).toBe('offline');
		listFormulas.mockResolvedValueOnce({ success: true, data: [formula('a')] });
		await loadFormulas('load', 'retry');
		expect(listOf('load', 'retry')).toMatchObject({ error: null, loaded: true });
	});

	it('puts a new formula first and replaces an existing one in place', () => {
		upsertFormula(formula('a', { preset_id: 'u', mode: 'x' }));
		upsertFormula(formula('b', { preset_id: 'u', mode: 'x' }));
		upsertFormula(formula('a', { preset_id: 'u', mode: 'x', name: 'renamed' }));
		expect(listOf('u', 'x').items.map((f) => [f.id, f.name])).toEqual([
			['b', 'b'],
			['a', 'renamed']
		]);
	});

	it('removes one formula and leaves other lists alone', () => {
		upsertFormula(formula('a', { preset_id: 'r', mode: 'x' }));
		upsertFormula(formula('b', { preset_id: 'r', mode: 'x' }));
		upsertFormula(formula('a', { preset_id: 'r', mode: 'y' }));
		removeFormula('r', 'x', 'a');
		removeFormula('r', 'x', 'missing');
		expect(listOf('r', 'x').items.map((f) => f.id)).toEqual(['b']);
		expect(listOf('r', 'y').items.map((f) => f.id)).toEqual(['a']);
	});
});
