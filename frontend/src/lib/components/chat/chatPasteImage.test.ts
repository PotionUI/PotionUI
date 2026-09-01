import { describe, it, expect } from 'vitest';
import { findImageItemIndex } from './chatPasteImage';

describe('findImageItemIndex', () => {
	it('returns -1 when there are no items', () => {
		expect(findImageItemIndex([])).toBe(-1);
	});

	it('returns -1 for a text-only paste', () => {
		expect(findImageItemIndex([{ type: 'text/plain' }])).toBe(-1);
	});

	it('finds an image among text items', () => {
		const items = [{ type: 'text/plain' }, { type: 'image/png' }];
		expect(findImageItemIndex(items)).toBe(1);
	});

	it('finds an image that comes before text', () => {
		const items = [{ type: 'image/png' }, { type: 'text/html' }];
		expect(findImageItemIndex(items)).toBe(0);
	});

	// Only the field's single value channel exists for chat vision - a paste
	// with several images can only ever attach one, so only the first match
	// is reported.
	it('reports only the first image when several are present', () => {
		const items = [{ type: 'image/png' }, { type: 'image/jpeg' }];
		expect(findImageItemIndex(items)).toBe(0);
	});

	it('matches any image subtype, not just png', () => {
		expect(findImageItemIndex([{ type: 'image/svg+xml' }])).toBe(0);
	});

	it('accepts an ArrayLike (e.g. a real DataTransferItemList), not just an array', () => {
		const arrayLike = { length: 2, 0: { type: 'text/plain' }, 1: { type: 'image/gif' } };
		expect(findImageItemIndex(arrayLike)).toBe(1);
	});
});
