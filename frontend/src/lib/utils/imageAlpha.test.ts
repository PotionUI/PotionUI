import { describe, it, expect } from 'vitest';
import { showAlphaCheckerboard } from './imageAlpha';

describe('showAlphaCheckerboard', () => {
	it('is true for an image file with real transparency', () => {
		expect(showAlphaCheckerboard({ file_type: 'IMAGE', has_alpha: true })).toBe(true);
	});

	it('is false for an opaque image file', () => {
		expect(showAlphaCheckerboard({ file_type: 'IMAGE', has_alpha: false })).toBe(false);
	});

	it('is false when has_alpha is absent', () => {
		expect(showAlphaCheckerboard({ file_type: 'IMAGE' })).toBe(false);
	});

	it('is false for a non-image file even when has_alpha is set', () => {
		expect(showAlphaCheckerboard({ file_type: 'VIDEO', has_alpha: true })).toBe(false);
	});

	it('is case-insensitive on file_type', () => {
		expect(showAlphaCheckerboard({ file_type: 'image', has_alpha: true })).toBe(true);
	});
});
