import { expect, test } from 'vitest';
import { DRAWER_KEY, DRAWER_MIN, DRAWER_WIDTH, clampDrawer, draggedDrawer, drawerMax, keyedDrawer, readDrawer, writeDrawer } from './drawer';

function memory(initial: Record<string, string> = {}) {
	const data = new Map<string, string>(Object.entries(initial));
	return {
		data,
		getItem: (key: string) => (data.has(key) ? (data.get(key) as string) : null),
		setItem: (key: string, value: unknown) => void data.set(key, String(value)),
		removeItem: (key: string) => void data.delete(key)
	};
}

test('the drawer is 40rem by default, never under 26rem and never over 65% of the tab', () => {
	expect(DRAWER_WIDTH).toBe(640);
	expect(DRAWER_MIN).toBe(416);
	expect(clampDrawer(100, 2000)).toBe(416);
	expect(clampDrawer(1600, 2000)).toBe(1300);
	expect(clampDrawer(700.4, 2000)).toBe(700);
	expect(clampDrawer('wide', 2000)).toBe(640);
	expect(clampDrawer(640, 800)).toBe(520);
	expect(drawerMax(500)).toBe(416);
	expect(clampDrawer(900, 500)).toBe(416);
	expect(clampDrawer(5000)).toBe(5000);
});

test('dragging the left edge left widens the drawer and right narrows it', () => {
	expect(draggedDrawer(640, 1000, 900, 2000)).toBe(740);
	expect(draggedDrawer(640, 1000, 1400, 2000)).toBe(416);
	expect(draggedDrawer(640, 1000, 0, 1200)).toBe(780);
});

test('arrow keys move the edge, Shift moves it further, Home resets', () => {
	expect(keyedDrawer(640, 'ArrowLeft', false, 2000)).toBe(656);
	expect(keyedDrawer(640, 'ArrowRight', false, 2000)).toBe(624);
	expect(keyedDrawer(640, 'ArrowLeft', true, 2000)).toBe(704);
	expect(keyedDrawer(420, 'ArrowRight', true, 2000)).toBe(416);
	expect(keyedDrawer(900, 'Home', false, 2000)).toBe(640);
	expect(keyedDrawer(640, 'Enter', false, 2000)).toBe(null);
});

test('the width is remembered in the viewer\'s storage and the default is not stored', () => {
	const storage = memory();
	writeDrawer(storage, 800);
	expect(storage.getItem(DRAWER_KEY)).toBe('800');
	expect(readDrawer(storage)).toBe(800);
	writeDrawer(storage, 640);
	expect(storage.data.has(DRAWER_KEY)).toBe(false);
	expect(readDrawer(storage)).toBe(640);
});

test('broken or blocked storage falls back to the default width', () => {
	expect(readDrawer(memory({ [DRAWER_KEY]: 'oops' }))).toBe(640);
	expect(readDrawer(memory({ [DRAWER_KEY]: '10' }))).toBe(416);
	const blocked = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); }, removeItem() { throw new Error('denied'); } };
	expect(readDrawer(blocked)).toBe(640);
	expect(() => writeDrawer(blocked, 700)).not.toThrow();
	expect(readDrawer(null)).toBe(640);
	expect(() => writeDrawer(null, 700)).not.toThrow();
});
