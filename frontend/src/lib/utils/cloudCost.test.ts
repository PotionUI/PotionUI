import { describe, it, expect } from 'vitest';
import {
	costCell,
	formatUsd,
	formatUsdExact,
	parseAmount,
	sortByAmountDesc,
	spendIsEmpty,
	spendNote,
	type CloudSpend
} from './cloudCost';

const spend = (overrides: Partial<CloudSpend> = {}): CloudSpend => ({
	from: null,
	to: null,
	total_usd: '0',
	entries: 0,
	unpriced: 0,
	by_backend: [],
	by_model: [],
	...overrides
});

describe('money formatting', () => {
	it('rounds to cents and keeps four decimals for the exact form', () => {
		expect(formatUsd('0.0731')).toBe('$0.07');
		expect(formatUsdExact('0.0731')).toBe('$0.0731');
		expect(formatUsd('1234.5')).toBe('$1,234.50');
	});

	it('shows a floor marker for amounts below half a cent and a dash for unknowns', () => {
		expect(formatUsd('0.0001')).toBe('<$0.01');
		expect(formatUsd('0')).toBe('$0.00');
		expect(formatUsd(null)).toBe('—');
		expect(formatUsd('abc')).toBe('—');
		expect(parseAmount('')).toBeNull();
	});
});

describe('costCell', () => {
	it('is a dash with no tooltip when a generation has no cost', () => {
		expect(costCell(null)).toEqual({ kind: 'none', text: '—', estimate: false, tooltip: null });
	});

	it('marks estimates and mixed sources and describes them', () => {
		const estimate = costCell({ amount_usd: '0.0731', source: 'estimate', entries: 1, unpriced: 0 });
		expect(estimate.text).toBe('$0.07');
		expect(estimate.estimate).toBe(true);
		expect(estimate.tooltip).toContain('$0.0731');
		expect(estimate.tooltip).toContain('Estimated');
		expect(costCell({ amount_usd: '1', source: 'mixed', entries: 2, unpriced: 0 }).estimate).toBe(true);
		expect(costCell({ amount_usd: '1', source: 'provider', entries: 1, unpriced: 0 }).estimate).toBe(false);
	});

	it('reports unpriced when the amount is unknown', () => {
		const cell = costCell({ amount_usd: null, source: 'unknown', entries: 2, unpriced: 2 });
		expect(cell.kind).toBe('unpriced');
		expect(cell.tooltip).toBe('No price is known for 2 jobs.');
	});

	it('mentions unpriced jobs that are left out of a known amount', () => {
		const cell = costCell({ amount_usd: '0.5', source: 'provider', entries: 3, unpriced: 1 });
		expect(cell.tooltip).toContain('1 job with no known price not included');
	});
});

describe('spend helpers', () => {
	it('sorts by amount descending with unpriced rows last', () => {
		const rows = [
			{ id: 'a', amount_usd: null },
			{ id: 'b', amount_usd: '0.2' },
			{ id: 'c', amount_usd: '1.5' }
		];
		expect(sortByAmountDesc(rows).map((r) => r.id)).toEqual(['c', 'b', 'a']);
		expect(rows[0].id).toBe('a');
	});

	it('treats no entries as empty', () => {
		expect(spendIsEmpty(null)).toBe(true);
		expect(spendIsEmpty(spend())).toBe(true);
		expect(spendIsEmpty(spend({ entries: 1, total_usd: '0.1' }))).toBe(false);
	});

	it('explains unpriced jobs in the note only when there are some', () => {
		expect(spendNote(spend({ entries: 2, unpriced: 0 }))).not.toContain('no known price');
		expect(spendNote(spend({ entries: 2, unpriced: 1 }))).toContain('1 job with no known price');
	});
});
