import { describe, expect, it } from 'vitest';
import { MIN_RANGE, clampRange, defaultRange, fromDrag, lengthOf, parseSeconds, toRunRange, validRange, withEnd, withStart } from './aiRange';

describe('the AI range on the timeline', () => {
	it('marks a drag in either direction and keeps it inside the clip', () => {
		expect(fromDrag(2, 4, 6.4)).toEqual({ start: 2, end: 4 });
		expect(fromDrag(4, 2, 6.4)).toEqual({ start: 2, end: 4 });
		expect(fromDrag(5, 9, 6.4)).toEqual({ start: 5, end: 6.4 });
		expect(clampRange({ start: -1, end: 3 }, 6.4)).toEqual({ start: 0, end: 3 });
	});

	it('moves the start and the end from the number fields without crossing', () => {
		const range = { start: 2, end: 4 };
		expect(withStart(range, 1.5, 6.4)).toEqual({ start: 1.5, end: 4 });
		expect(withStart(range, 9, 6.4)).toEqual({ start: 4 - MIN_RANGE, end: 4 });
		expect(withEnd(range, 5.25, 6.4)).toEqual({ start: 2, end: 5.25 });
		expect(withEnd(range, 1, 6.4)).toEqual({ start: 2, end: 2 + MIN_RANGE });
		expect(withEnd(range, 99, 6.4)).toEqual({ start: 2, end: 6.4 });
	});

	it('starts a range of two seconds at the playhead and keeps it inside a short clip', () => {
		expect(defaultRange(1, 6.4)).toEqual({ start: 1, end: 3 });
		expect(defaultRange(6, 6.4)).toEqual({ start: 4.4, end: 6.4 });
		expect(defaultRange(0, 1.5)).toEqual({ start: 0, end: 1.5 });
		expect(defaultRange(0, 0)).toBeNull();
	});

	it('treats nothing marked, or a sliver, as the whole clip', () => {
		expect(validRange(null, 6.4)).toBeNull();
		expect(validRange({ start: 2, end: 2.01 }, 6.4)).toBeNull();
		expect(validRange({ start: 2, end: 4 }, 6.4)).toEqual({ start: 2, end: 4 });
		expect(lengthOf(null, 6.4)).toBe(6.4);
		expect(lengthOf({ start: 2, end: 4 }, 6.4)).toBe(2);
	});

	it('sends the range in seconds of the clip the model sees: speed and trim steps beneath move it', () => {
		expect(toRunRange({ start: 2, end: 4 }, 6.4, [])).toEqual({ start_s: 2, end_s: 4 });
		expect(toRunRange({ start: 2, end: 4 }, 6.4, [{ kind: 'speed', speed: 2 }])).toEqual({ start_s: 1, end_s: 2 });
		expect(toRunRange({ start: 2, end: 4 }, 6.4, [{ kind: 'trim', start: 1, end: 5 }])).toEqual({ start_s: 1, end_s: 3 });
		expect(toRunRange(null, 6.4, [])).toBeNull();
	});

	it('reads typed seconds and refuses the rest', () => {
		expect(parseSeconds('2.5')).toBe(2.5);
		expect(parseSeconds(' 3,25 ')).toBe(3.25);
		expect(parseSeconds('')).toBeNull();
		expect(parseSeconds('-1')).toBeNull();
		expect(parseSeconds('abc')).toBeNull();
	});
});
