import { describe, it, expect } from 'vitest';
import { toggleOpen, sectionBoxClass, sectionBodyClass } from './detailSection';

describe('toggleOpen', () => {
	it('flips open to closed', () => {
		expect(toggleOpen(true)).toBe(false);
	});

	it('flips closed to open', () => {
		expect(toggleOpen(false)).toBe(true);
	});
});

describe('sectionBoxClass', () => {
	it('carries the standard box tokens', () => {
		const cls = sectionBoxClass(true);
		expect(cls).toContain('rounded-lg');
		expect(cls).toContain('border-line');
		expect(cls).toContain('bg-surface-1');
		expect(cls).toContain('shadow-raised');
	});

	it('adds overflow-hidden only when unpadded (edge-bleeding content)', () => {
		expect(sectionBoxClass(true)).not.toContain('overflow-hidden');
		expect(sectionBoxClass(false)).toContain('overflow-hidden');
	});
});

describe('sectionBodyClass', () => {
	it('pads by default', () => {
		expect(sectionBodyClass(true)).toBe('px-4 sm:px-5 py-4');
	});

	it('is empty when unpadded', () => {
		expect(sectionBodyClass(false)).toBe('');
	});
});
