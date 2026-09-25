import { describe, it, expect } from 'vitest';
import { clearAll, clearPage, pageSelectionState, selectPage, toggleRow } from './selection';

describe('pageSelectionState', () => {
	it('is none when nothing on the page is selected', () => {
		expect(pageSelectionState(['a', 'b'], new Set())).toBe('none');
		expect(pageSelectionState(['a', 'b'], new Set(['c']))).toBe('none');
	});

	it('is some when only part of the page is selected (indeterminate)', () => {
		expect(pageSelectionState(['a', 'b'], new Set(['a']))).toBe('some');
	});

	it('is all when every row on the page is selected', () => {
		expect(pageSelectionState(['a', 'b'], new Set(['a', 'b', 'z']))).toBe('all');
	});

	it('is none for an empty page', () => {
		expect(pageSelectionState([], new Set(['a']))).toBe('none');
	});
});

describe('toggleRow', () => {
	it('adds an unselected row and removes a selected one, without mutating the input', () => {
		const original = new Set(['a']);
		const added = toggleRow(original, 'b');
		expect(added).toEqual(new Set(['a', 'b']));
		expect(original).toEqual(new Set(['a']));

		const removed = toggleRow(added, 'a');
		expect(removed).toEqual(new Set(['b']));
	});
});

describe('selectPage / clearPage', () => {
	it('selectPage unions the page ids into the selection', () => {
		expect(selectPage(new Set(['z']), ['a', 'b'])).toEqual(new Set(['z', 'a', 'b']));
	});

	it('clearPage removes only the page ids, keeping selections from other pages', () => {
		expect(clearPage(new Set(['z', 'a', 'b']), ['a', 'b'])).toEqual(new Set(['z']));
	});
});

describe('clearAll', () => {
	it('returns an empty set', () => {
		expect(clearAll()).toEqual(new Set());
	});
});
