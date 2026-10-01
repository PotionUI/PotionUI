import { describe, expect, it, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { sideDrawer } from './sideDrawer';

describe('sideDrawer', () => {
	beforeEach(() => {
		sideDrawer.close('sessions');
		sideDrawer.close('formulas');
	});

	it('starts with nothing open', () => {
		expect(get(sideDrawer)).toBeNull();
	});

	it('opening one drawer after another leaves only the second', () => {
		sideDrawer.open('sessions');
		sideDrawer.open('formulas');
		expect(get(sideDrawer)).toBe('formulas');
	});

	it('closing a drawer that is not the open one changes nothing', () => {
		sideDrawer.open('formulas');
		sideDrawer.close('sessions');
		expect(get(sideDrawer)).toBe('formulas');
	});

	it('closing the open drawer leaves nothing open', () => {
		sideDrawer.open('sessions');
		sideDrawer.close('sessions');
		expect(get(sideDrawer)).toBeNull();
	});
});
