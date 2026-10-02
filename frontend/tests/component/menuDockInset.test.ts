// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach } from 'vitest';
import { dockInsetFor, computeFlippedMenuPosition, MENU_GAP, MENU_EDGE_GUTTER } from '../../src/lib/utils/menuPosition';

const VIEWPORT_HEIGHT = 900;

function rectOf(top: number, bottom: number) {
	return () => ({ top, bottom, left: 100, right: 200, width: 100, height: bottom - top, x: 100, y: top, toJSON() {} }) as DOMRect;
}

function addDock(top: number, height: number) {
	const dock = document.createElement('div');
	dock.setAttribute('data-menu-dock', '');
	dock.getBoundingClientRect = rectOf(top, top + height);
	document.body.appendChild(dock);
	return dock;
}

function addTrigger(parent: HTMLElement = document.body, top = 600, bottom = 640) {
	const trigger = document.createElement('button');
	trigger.getBoundingClientRect = rectOf(top, bottom);
	parent.appendChild(trigger);
	return trigger;
}

beforeEach(() => {
	Object.defineProperty(window, 'innerHeight', { value: VIEWPORT_HEIGHT, configurable: true, writable: true });
});

afterEach(() => {
	document.body.innerHTML = '';
});

describe('dockInsetFor', () => {
	it('is zero when no dock is on the page', () => {
		expect(dockInsetFor(addTrigger())).toBe(0);
	});

	it('is the distance from the dock top to the bottom of the viewport', () => {
		addDock(818, 60);
		expect(dockInsetFor(addTrigger())).toBe(82);
	});

	it('is zero for a dock that is not laid out', () => {
		addDock(900, 0);
		expect(dockInsetFor(addTrigger())).toBe(0);
	});

	it('is zero for a trigger inside a dialog', () => {
		addDock(818, 60);
		const dialog = document.createElement('div');
		dialog.setAttribute('role', 'dialog');
		document.body.appendChild(dialog);
		expect(dockInsetFor(addTrigger(dialog))).toBe(0);
	});

	it('ignores an element that only has the old styling class', () => {
		const lookalike = addDock(818, 60);
		lookalike.removeAttribute('data-menu-dock');
		lookalike.className = 'generation-panel';
		expect(dockInsetFor(addTrigger())).toBe(0);
	});
});

describe('computeFlippedMenuPosition with a real dock', () => {
	it('flips upward and floors against the dock when the dock eats the room below', () => {
		addDock(818, 60);
		const trigger = addTrigger(document.body, 700, 740);

		const docked = computeFlippedMenuPosition(trigger, { heightEstimate: 120 });

		expect(docked.bottom).toBe(VIEWPORT_HEIGHT - 700 + MENU_GAP);
		expect(docked.top).toBeUndefined();
		expect(docked.maxHeight).toBe(700 - MENU_GAP - MENU_EDGE_GUTTER);
	});

	it('stays below when there is no dock and the menu fits', () => {
		const trigger = addTrigger(document.body, 700, 740);

		const free = computeFlippedMenuPosition(trigger, { heightEstimate: 120 });

		expect(free.top).toBe(740 + MENU_GAP);
		expect(free.maxHeight).toBe(VIEWPORT_HEIGHT - 740 - MENU_GAP - MENU_EDGE_GUTTER);
	});
});
