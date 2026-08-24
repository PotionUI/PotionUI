import { describe, expect, it } from 'vitest';
import { SECTION_WELL_INSET, accumulatedInset } from './rowInset';

describe('accumulatedInset', () => {
	it('treats a missing parent inset as zero', () => {
		expect(accumulatedInset(undefined, SECTION_WELL_INSET)).toBe(SECTION_WELL_INSET);
	});

	it('adds on top of a parent inset for nested sections', () => {
		expect(accumulatedInset(SECTION_WELL_INSET, SECTION_WELL_INSET)).toBe(SECTION_WELL_INSET * 2);
	});
});
