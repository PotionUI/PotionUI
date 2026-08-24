import { describe, it, expect } from 'vitest';
import { getDefaultVariant, resolveVariant, sortVariants } from './variants';
import type { PresetModeVariant } from '$lib/types/api';

const variants: PresetModeVariant[] = [
	{ name: 'fast', label: 'Fast', default: false, order: 1 },
	{ name: 'quality', label: 'Quality', default: true, order: 0 },
	{ name: 'draft', label: 'Draft', default: false, order: 2 }
];

describe('getDefaultVariant', () => {
	it('returns the variant flagged default', () => {
		expect(getDefaultVariant(variants)?.name).toBe('quality');
	});

	it('falls back to the first variant when none is flagged default', () => {
		const noDefault = variants.map((v) => ({ ...v, default: false }));
		expect(getDefaultVariant(noDefault)?.name).toBe('fast');
	});

	it('returns null for empty/missing variants', () => {
		expect(getDefaultVariant([])).toBeNull();
		expect(getDefaultVariant(undefined)).toBeNull();
		expect(getDefaultVariant(null)).toBeNull();
	});
});

describe('resolveVariant', () => {
	it('keeps the requested variant when it still exists', () => {
		expect(resolveVariant(variants, 'draft')).toBe('draft');
	});

	it('falls back to the default variant when requested no longer exists', () => {
		expect(resolveVariant(variants, 'removed')).toBe('quality');
	});

	it('falls back to the default variant when nothing is requested', () => {
		expect(resolveVariant(variants, null)).toBe('quality');
		expect(resolveVariant(variants, undefined)).toBe('quality');
	});

	it('returns null when the mode has no variants', () => {
		expect(resolveVariant([], 'anything')).toBeNull();
		expect(resolveVariant(undefined, 'anything')).toBeNull();
	});
});

describe('sortVariants', () => {
	it('sorts by order ascending', () => {
		expect(sortVariants(variants).map((v) => v.name)).toEqual(['quality', 'fast', 'draft']);
	});

	it('handles missing input', () => {
		expect(sortVariants(undefined)).toEqual([]);
		expect(sortVariants(null)).toEqual([]);
	});
});
