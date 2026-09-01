import { describe, it, expect } from 'vitest';
import { kvItemValueClass, kvItemWrapperClass } from './kvItem';

describe('kvItemValueClass', () => {
	it('adds mono + tabular-nums for numeric values', () => {
		const cls = kvItemValueClass(true);
		expect(cls).toContain('font-mono');
		expect(cls).toContain('tabular-nums');
	});

	it('is plain text for non-numeric values', () => {
		const cls = kvItemValueClass(false);
		expect(cls).not.toContain('font-mono');
		expect(cls).not.toContain('tabular-nums');
	});
});

describe('kvItemWrapperClass', () => {
	it('spans both columns when full', () => {
		expect(kvItemWrapperClass(true)).toBe('sm:col-span-2');
	});

	it('is unset otherwise', () => {
		expect(kvItemWrapperClass(false)).toBe('');
	});
});
