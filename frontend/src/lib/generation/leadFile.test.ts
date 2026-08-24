import { describe, expect, it } from 'vitest';
import { isDerivedItem, leadIndex } from './leadFile';

describe('leadIndex', () => {
	it('returns 0 for an empty list', () => {
		expect(leadIndex([])).toBe(0);
	});

	it('returns 0 when nothing is derived (current behavior unchanged)', () => {
		expect(leadIndex([{}, {}, {}])).toBe(0);
		expect(leadIndex([{ derived: false }, { is_derived: false }])).toBe(0);
	});

	it('prefers the derived item over the first', () => {
		expect(leadIndex([{}, { derived: true }])).toBe(1);
	});

	it('prefers the NEWEST derived item when several exist', () => {
		// Krea-2 enhance with quantity 2: base at 0..1, enhanced at 2..3.
		expect(leadIndex([{}, {}, { derived: true }, { derived: true }])).toBe(3);
	});

	it('reads the persisted-file spelling is_derived', () => {
		expect(leadIndex([{ is_derived: false }, { is_derived: true }])).toBe(1);
	});

	it('ignores null/undefined entries and non-boolean flags', () => {
		expect(leadIndex([null, undefined, { derived: true }, null])).toBe(2);
		expect(leadIndex([{ derived: undefined }, { is_derived: undefined }])).toBe(0);
	});
});

describe('isDerivedItem', () => {
	it('accepts either flag spelling, strictly boolean true', () => {
		expect(isDerivedItem({ derived: true })).toBe(true);
		expect(isDerivedItem({ is_derived: true })).toBe(true);
		expect(isDerivedItem({ derived: false })).toBe(false);
		expect(isDerivedItem({})).toBe(false);
		expect(isDerivedItem(null)).toBe(false);
	});
});
