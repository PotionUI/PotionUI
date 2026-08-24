import { describe, it, expect } from 'vitest';
import { resolveNegativeApplicability } from './negativeApplied';

describe('resolveNegativeApplicability', () => {
	it('is unknown when there is no form data', () => {
		expect(resolveNegativeApplicability(undefined)).toBe('unknown');
		expect(resolveNegativeApplicability(null)).toBe('unknown');
	});

	it('is unknown when no guidance field is present (no guidance concept)', () => {
		expect(resolveNegativeApplicability({ steps: 8 })).toBe('unknown');
	});

	it('marks inert when the resolved cfg is <= 1 (Z-Image turbo)', () => {
		expect(resolveNegativeApplicability({ cfg: 1.0 })).toBe('inert');
		expect(resolveNegativeApplicability({ cfg: 0 })).toBe('inert');
	});

	it('marks applied when the resolved cfg is > 1', () => {
		expect(resolveNegativeApplicability({ cfg: 4.0 })).toBe('applied');
	});

	it('honors the alternate cfg_scale field name', () => {
		expect(resolveNegativeApplicability({ cfg_scale: 1.0 })).toBe('inert');
		expect(resolveNegativeApplicability({ cfg_scale: 7.5 })).toBe('applied');
	});

	it("ignores Flux's distilled `guidance` field (not true CFG)", () => {
		// Flux hardcodes guidance_scale = 1.0; its `guidance` form field is the
		// distilled guidance embedding and must not be read as CFG. No cfg field
		// -> unknown -> no notice (the backend still records it honestly).
		expect(resolveNegativeApplicability({ guidance: 3.5 })).toBe('unknown');
	});

	it('stays applied when NAG forces the negative at guidance 1', () => {
		expect(resolveNegativeApplicability({ cfg: 1.0, nag_scale: 1.5 })).toBe('applied');
	});

	it('is inert when NAG is present but off at guidance 1', () => {
		expect(resolveNegativeApplicability({ cfg: 1.0, nag_scale: 1.0 })).toBe('inert');
	});

	it('coerces numeric strings from the form', () => {
		expect(resolveNegativeApplicability({ cfg: '1' })).toBe('inert');
		expect(resolveNegativeApplicability({ cfg: '4' })).toBe('applied');
	});

	it('is unknown when the guidance value is not a number', () => {
		expect(resolveNegativeApplicability({ cfg: '' })).toBe('unknown');
		expect(resolveNegativeApplicability({ cfg: 'auto' })).toBe('unknown');
	});

	it('respects a preset-declared descriptor over the conventions', () => {
		expect(
			resolveNegativeApplicability(
				{ my_guidance: 1.0, my_nag: 2.0 },
				{ guidance_field: 'my_guidance', nag_field: 'my_nag' }
			)
		).toBe('applied');
		expect(
			resolveNegativeApplicability({ my_guidance: 1.0 }, { guidance_field: 'my_guidance' })
		).toBe('inert');
	});
});
