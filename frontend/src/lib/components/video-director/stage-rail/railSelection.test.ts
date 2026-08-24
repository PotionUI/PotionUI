import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { railSelection, selectRailObject, clearRailSelection, isRailObjectSelected } from './railSelection';

describe('railSelection', () => {
	beforeEach(() => clearRailSelection());

	it('starts with nothing selected', () => {
		expect(get(railSelection)).toBeNull();
	});

	it('selecting an object replaces any prior selection (exactly one at a time)', () => {
		selectRailObject('shot', 's1');
		expect(get(railSelection)).toEqual({ kind: 'shot', id: 's1' });
		selectRailObject('keyframe', 'kf-1');
		expect(get(railSelection)).toEqual({ kind: 'keyframe', id: 'kf-1' });
	});

	it('clearRailSelection resets to null', () => {
		selectRailObject('audio', 'a1');
		clearRailSelection();
		expect(get(railSelection)).toBeNull();
	});

	it('isRailObjectSelected matches only the current kind+id pair', () => {
		selectRailObject('seam', 'seam-s1-s2');
		expect(isRailObjectSelected(get(railSelection), 'seam', 'seam-s1-s2')).toBe(true);
		expect(isRailObjectSelected(get(railSelection), 'seam', 'seam-s2-s3')).toBe(false);
		expect(isRailObjectSelected(get(railSelection), 'shot', 'seam-s1-s2')).toBe(false);
		expect(isRailObjectSelected(null, 'shot', 's1')).toBe(false);
	});
});
