import { describe, expect, it } from 'vitest';
import { markBytes, trimMarks, type MarkLike } from './undoMarks';

const base: MarkLike = { kind: 'stack' };
const pixels = (bytes: number): MarkLike => ({ kind: 'pixels', command: { bytes } });
const total = (marks: MarkLike[]) => marks.reduce((sum, mark) => sum + markBytes(mark), 0);

describe('trimMarks', () => {
	it('keeps everything under both the count and the byte budget', () => {
		const marks = [base, pixels(10), pixels(10)];
		expect(trimMarks(marks, 100, 10)).toBe(marks);
	});

	it('drops the oldest marks once the pixel bytes pass the budget', () => {
		const marks = [pixels(60), base, pixels(60), pixels(60)];
		const kept = trimMarks(marks, 130);
		expect(kept).toEqual([base, pixels(60), pixels(60)]);
		expect(total(kept)).toBeLessThanOrEqual(130);
	});

	it('always keeps the newest mark even when it alone is over the budget', () => {
		expect(trimMarks([pixels(10), pixels(500)], 100)).toEqual([pixels(500)]);
	});

	it('still caps by count', () => {
		const marks = Array.from({ length: 6 }, () => base);
		expect(trimMarks(marks, 100, 4)).toHaveLength(4);
	});

	it('holds a 4K history of whole-document patches to the budget', () => {
		const patch = 2 * 4096 * 4096 * 4;
		const budget = 256 * 1024 * 1024;
		let marks: MarkLike[] = [];
		for (let i = 0; i < 50; i++) marks = trimMarks([...marks, pixels(patch)], budget);
		expect(total(marks)).toBeLessThanOrEqual(budget);
		expect(marks).toHaveLength(2);
	});
});
