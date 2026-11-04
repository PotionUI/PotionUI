import { expect, test } from 'vitest';
import { COLUMN_WIDTH, clampWidth, columnWidth, keyedWidth, readWidths, widthsKey, withWidth, writeWidths } from './columns';

function memory(initial: Record<string, string> = {}) {
	const data = new Map<string, string>(Object.entries(initial));
	return {
		data,
		getItem: (key: string) => (data.has(key) ? (data.get(key) as string) : null),
		setItem: (key: string, value: unknown) => void data.set(key, String(value)),
		removeItem: (key: string) => void data.delete(key)
	};
}

test('a column is 384px wide by default and stays between 320 and 768', () => {
	expect(COLUMN_WIDTH).toBe(384);
	expect(clampWidth(100)).toBe(320);
	expect(clampWidth(2000)).toBe(768);
	expect(clampWidth(500.4)).toBe(500);
	expect(clampWidth('wide')).toBe(384);
	expect(columnWidth([], 2)).toBe(384);
	expect(columnWidth([null, 600], 1)).toBe(600);
});

test('arrow keys step 16px, Shift steps 64px, Home resets, other keys do nothing', () => {
	expect(keyedWidth(384, 'ArrowRight')).toBe(400);
	expect(keyedWidth(384, 'ArrowLeft')).toBe(368);
	expect(keyedWidth(384, 'ArrowRight', true)).toBe(448);
	expect(keyedWidth(330, 'ArrowLeft', true)).toBe(320);
	expect(keyedWidth(760, 'ArrowRight')).toBe(768);
	expect(keyedWidth(600, 'Home')).toBe(384);
	expect(keyedWidth(600, 'Enter')).toBe(null);
});

test('widths are kept per column and the default width is not stored', () => {
	expect(withWidth([], 1, 600)).toStrictEqual([null, 600]);
	expect(withWidth([null, 600], 1, 384)).toStrictEqual([]);
	expect(withWidth([500], 2, 900)).toStrictEqual([500, null, 768]);
});

test('widths are remembered per elixir in the viewer\'s storage', () => {
	const storage = memory();
	writeWidths(storage, 'abc', [null, 512]);
	expect(storage.getItem(widthsKey('abc'))).toBe('[null,512]');
	expect(readWidths(storage, 'abc')).toStrictEqual([null, 512]);
	expect(readWidths(storage, 'other')).toStrictEqual([]);
	writeWidths(storage, 'abc', []);
	expect(storage.data.has(widthsKey('abc'))).toBe(false);
	expect(widthsKey('abc')).not.toBe(widthsKey('abd'));
});

test('broken or blocked storage never breaks the lane', () => {
	expect(readWidths(memory({ [widthsKey('x')]: '{oops' }), 'x')).toStrictEqual([]);
	expect(readWidths(memory({ [widthsKey('x')]: '[9999,"a"]' }), 'x')).toStrictEqual([768, null]);
	const blocked = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); }, removeItem() { throw new Error('denied'); } };
	expect(readWidths(blocked, 'x')).toStrictEqual([]);
	expect(() => writeWidths(blocked, 'x', [500])).not.toThrow();
	expect(readWidths(null, 'x')).toStrictEqual([]);
	expect(() => writeWidths(null, 'x', [500])).not.toThrow();
});
