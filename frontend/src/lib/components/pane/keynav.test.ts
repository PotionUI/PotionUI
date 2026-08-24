// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { nextIndex, focusableRows } from './keynav';

describe('nextIndex', () => {
	it('returns -1 for a degenerate empty list', () => {
		expect(nextIndex(0, -1, 'ArrowDown')).toBe(-1);
		expect(nextIndex(0, -1, 'Home')).toBe(-1);
	});

	it('a single-row list resolves every key to index 0', () => {
		expect(nextIndex(1, -1, 'ArrowDown')).toBe(0);
		expect(nextIndex(1, 0, 'ArrowDown')).toBe(0);
		expect(nextIndex(1, 0, 'ArrowUp')).toBe(0);
		expect(nextIndex(1, -1, 'Home')).toBe(0);
		expect(nextIndex(1, -1, 'End')).toBe(0);
	});

	it('Home always goes to 0, End always goes to the last index', () => {
		expect(nextIndex(5, 2, 'Home')).toBe(0);
		expect(nextIndex(5, 2, 'End')).toBe(4);
	});

	it('ArrowDown clamps at the last index instead of wrapping', () => {
		expect(nextIndex(3, 1, 'ArrowDown')).toBe(2);
		expect(nextIndex(3, 2, 'ArrowDown')).toBe(2);
	});

	it('ArrowUp clamps at 0 instead of wrapping', () => {
		expect(nextIndex(3, 1, 'ArrowUp')).toBe(0);
		expect(nextIndex(3, 0, 'ArrowUp')).toBe(0);
	});

	it('starts from an edge when nothing is focused (current = -1)', () => {
		expect(nextIndex(3, -1, 'ArrowDown')).toBe(0);
		expect(nextIndex(3, -1, 'ArrowUp')).toBe(2);
	});
});

describe('focusableRows', () => {
	it('finds [data-pane-row] elements in DOM order, skipping data-disabled ones', () => {
		document.body.innerHTML = `
			<div id="c">
				<div data-pane-row id="r1"></div>
				<div data-pane-row data-disabled id="r2"></div>
				<div data-pane-row id="r3"></div>
			</div>
		`;
		const container = document.getElementById('c') as HTMLElement;
		expect(focusableRows(container).map((r) => r.id)).toEqual(['r1', 'r3']);
	});

	it('returns an empty array when the container has no rows', () => {
		document.body.innerHTML = `<div id="empty"></div>`;
		const container = document.getElementById('empty') as HTMLElement;
		expect(focusableRows(container)).toEqual([]);
	});
});
