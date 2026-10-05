import { describe, it, expect } from 'vitest';
import type { LimitRow } from './meApi';
import { closestLimit, formatAmount, leftNote, percentOf } from './limitView';

const row = (over: Partial<LimitRow>): LimitRow => ({
	kind: 'k',
	label: 'K',
	used: 0,
	limit: 100,
	format: 'count',
	resets_at: null,
	state: 'ok',
	percent: null,
	enforced: true,
	...over
});

describe('limitView', () => {
	it('formats storage in gigabytes', () => {
		expect(formatAmount(row({ format: 'bytes', used: 18.6 * 2 ** 30, limit: 20 * 2 ** 30 }))).toBe('18.6 / 20 GB');
	});

	it('switches to megabytes for a small limit', () => {
		expect(formatAmount(row({ format: 'bytes', used: 120 * 2 ** 20, limit: 500 * 2 ** 20 }))).toBe('120 / 500 MB');
	});

	it('formats counts plainly', () => {
		expect(formatAmount(row({ used: 37, limit: 100 }))).toBe('37 / 100');
	});

	it('shows only a percent for a hidden-money limit', () => {
		expect(formatAmount(row({ format: 'percent', used: 36, limit: 50 }))).toBe('72%');
	});

	it('trusts the server percent when the numbers are hidden', () => {
		const hidden = row({ format: 'percent', used: 0, limit: 0, percent: 72 });
		expect(formatAmount(hidden)).toBe('72%');
		expect(percentOf(hidden)).toBe(72);
	});

	it('clamps the bar percent', () => {
		expect(percentOf(row({ used: 150, limit: 100 }))).toBe(100);
		expect(percentOf(row({ used: 1, limit: 0 }))).toBe(0);
	});

	it('picks the limit closest to full', () => {
		const rows = [row({ kind: 'a', used: 10 }), row({ kind: 'b', used: 90 }), row({ kind: 'c', used: 40 })];
		expect(closestLimit(rows)?.kind).toBe('b');
		expect(closestLimit([])).toBeNull();
	});

	it('says how much storage is left', () => {
		expect(leftNote(row({ format: 'bytes', used: 18.6 * 2 ** 30, limit: 20 * 2 ** 30 }))).toBe('1.4 GB left');
		expect(leftNote(row({ format: 'count' }))).toBeNull();
	});
});

describe('closestLimit with per-file limits', () => {
	it('never picks a per-file limit, whose used count is zero against a limit', () => {
		const perFile = row({ kind: 'upload_file_size', perItem: true, used: 0, limit: 0, percent: 100 });
		const other = row({ kind: 'a', used: 1, limit: 100 });
		expect(closestLimit([perFile, other])?.kind).toBe('a');
		expect(closestLimit([perFile])).toBeNull();
	});
});
