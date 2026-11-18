import { describe, expect, it } from 'vitest';
import { RANGE_REQUIRED_REASON, capRange, formatCap, rangeBlockReason } from './aiRange';
import { blockedReason } from '$lib/tool-tabs/image/generate/mark';

describe('the cap on a marked range', () => {
	it('leaves a range under the cap alone', () => {
		const next = { start: 2, end: 6 };
		expect(capRange(next, null, 14, 60)).toBe(next);
		expect(capRange(next, null, null, 60)).toBe(next);
	});

	it('clamps a drag past the cap by moving the end, or the start when the start was dragged', () => {
		expect(capRange({ start: 2, end: 20 }, { start: 2, end: 6 }, 14, 60)).toEqual({ start: 2, end: 16 });
		expect(capRange({ start: 2, end: 20 }, { start: 10, end: 20 }, 14, 60)).toEqual({ start: 6, end: 20 });
	});

	it('shows the cap as seconds', () => {
		expect(formatCap(14)).toBe('14 s');
		expect(formatCap(7.5)).toBe('7.5 s');
	});

	it('blocks a whole clip longer than the cap with the reason, and not a clip within it', () => {
		expect(rangeBlockReason(null, 30, 14)).toBe('Mark a section of up to 14 s');
		expect(rangeBlockReason(null, 10, 14)).toBeNull();
		expect(rangeBlockReason({ start: 0, end: 5 }, 30, 14)).toBeNull();
		expect(rangeBlockReason(null, 30, null)).toBeNull();
	});

	it('blocks a task that needs a range until one is marked', () => {
		expect(rangeBlockReason(null, 5, null, true)).toBe(RANGE_REQUIRED_REASON);
		expect(RANGE_REQUIRED_REASON).toBe('Mark the frames to fix');
		expect(rangeBlockReason({ start: 1, end: 2 }, 5, null, true)).toBeNull();
	});

	it('is the reason the Generate mark shows', () => {
		const base = { hasSetup: true, promptRequired: false, prompt: '', regionRequired: false, hasArea: false, redoArea: false };
		expect(blockedReason({ ...base, rangeBlock: 'Mark a section of up to 14 s' })).toBe('Mark a section of up to 14 s');
		expect(blockedReason(base)).toBeNull();
	});
});
