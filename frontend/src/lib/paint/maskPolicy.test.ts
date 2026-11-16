import { describe, expect, it } from 'vitest';
import { decideMaskFate } from './maskPolicy';

describe('decideMaskFate', () => {
	it('has nothing to say when there is no mask', () => {
		expect(
			decideMaskFate({
				hasMask: false,
				geometryChanged: true,
				sourceReplaced: true
			})
		).toEqual({
			fate: 'none',
			notice: null
		});
	});

	it('keeps the mask when only pixels changed', () => {
		const decision = decideMaskFate({
			hasMask: true,
			geometryChanged: false,
			sourceReplaced: false
		});
		expect(decision.fate).toBe('keep');
		expect(decision.notice).toMatch(/kept/);
	});

	it('clears the mask after any geometry change', () => {
		const decision = decideMaskFate({
			hasMask: true,
			geometryChanged: true,
			sourceReplaced: false
		});
		expect(decision.fate).toBe('clear');
		expect(decision.notice).toMatch(/cropped, resized, rotated or flipped/);
	});

	it('clears the mask when a different image was opened', () => {
		const decision = decideMaskFate({
			hasMask: true,
			geometryChanged: false,
			sourceReplaced: true
		});
		expect(decision.fate).toBe('clear');
		expect(decision.notice).toMatch(/different image/);
	});
});
