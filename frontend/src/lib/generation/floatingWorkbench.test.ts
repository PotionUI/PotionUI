import { describe, it, expect } from 'vitest';
import {
	openFloatingWorkbench,
	closeFloatingWorkbench,
	toggleFloatingWorkbench,
	resizeFloatingWorkbench,
	type FloatingWorkbenchBounds
} from './floatingWorkbench';

describe('openFloatingWorkbench', () => {
	it('sets workbenchFloating', () => {
		expect(openFloatingWorkbench()).toEqual({ workbenchFloating: true });
	});
});

describe('closeFloatingWorkbench', () => {
	it('clears workbenchFloating', () => {
		expect(closeFloatingWorkbench()).toEqual({ workbenchFloating: false });
	});
});

describe('toggleFloatingWorkbench', () => {
	it('opens when not floating', () => {
		expect(toggleFloatingWorkbench({ workbenchFloating: false })).toEqual({ workbenchFloating: true });
	});

	it('opens when undefined', () => {
		expect(toggleFloatingWorkbench({})).toEqual({ workbenchFloating: true });
	});

	it('closes when already floating', () => {
		expect(toggleFloatingWorkbench({ workbenchFloating: true })).toEqual({ workbenchFloating: false });
	});

	it('round-trips open -> close', () => {
		const opened = toggleFloatingWorkbench({ workbenchFloating: false });
		const closed = toggleFloatingWorkbench(opened);
		expect(closed).toEqual({ workbenchFloating: false });
	});
});

describe('resizeFloatingWorkbench', () => {
	const bounds: FloatingWorkbenchBounds = { minWidth: 360, maxWidth: 1200, minHeight: 240, maxHeight: 900 };

	it('a right-edge drag (dy=0) changes only width', () => {
		const next = resizeFloatingWorkbench({ width: 640, height: 600 }, { dx: 100, dy: 0 }, bounds);
		expect(next).toEqual({ width: 740, height: 600 });
	});

	it('a bottom-edge drag (dx=0) changes only height', () => {
		const next = resizeFloatingWorkbench({ width: 640, height: 600 }, { dx: 0, dy: 50 }, bounds);
		expect(next).toEqual({ width: 640, height: 650 });
	});

	it('a corner drag changes both', () => {
		const next = resizeFloatingWorkbench({ width: 640, height: 600 }, { dx: 120, dy: 80 }, bounds);
		expect(next).toEqual({ width: 760, height: 680 });
	});

	it('shrinking clamps at the minimum', () => {
		const next = resizeFloatingWorkbench({ width: 400, height: 260 }, { dx: -500, dy: -500 }, bounds);
		expect(next).toEqual({ width: 360, height: 240 });
	});

	it('growing clamps at the maximum', () => {
		const next = resizeFloatingWorkbench({ width: 1100, height: 850 }, { dx: 500, dy: 500 }, bounds);
		expect(next).toEqual({ width: 1200, height: 900 });
	});

	it('a negative delta shrinks', () => {
		const next = resizeFloatingWorkbench({ width: 640, height: 600 }, { dx: -40, dy: -20 }, bounds);
		expect(next).toEqual({ width: 600, height: 580 });
	});
});
