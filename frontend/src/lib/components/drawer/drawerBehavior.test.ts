import { describe, expect, it, vi } from 'vitest';
import { KEEP_OPEN_MIN_WIDTH, effectiveKeepOpen, handleDrawerKeydown } from './drawerBehavior';

describe('drawer behavior', () => {
	it('only keeps the drawer open on wide viewports', () => {
		expect(effectiveKeepOpen(true, KEEP_OPEN_MIN_WIDTH)).toBe(true);
		expect(effectiveKeepOpen(true, KEEP_OPEN_MIN_WIDTH - 1)).toBe(false);
		expect(effectiveKeepOpen(false, 2000)).toBe(false);
	});
});

describe('drawer escape', () => {
	function press(defaultPrevented: boolean) {
		const target = {};
		const onClose = vi.fn();
		const event = { key: 'Escape', target, defaultPrevented, preventDefault: vi.fn(), stopPropagation: vi.fn() } as unknown as KeyboardEvent;
		const root = { contains: (node: unknown) => node === target } as unknown as HTMLElement;
		handleDrawerKeydown(event, { root, searchEl: undefined, keepOpen: true, onClose });
		return onClose;
	}

	it('closes the drawer on Escape', () => {
		expect(press(false)).toHaveBeenCalledTimes(1);
	});

	it('leaves the drawer open when something inside already used the Escape', () => {
		expect(press(true)).not.toHaveBeenCalled();
	});
});
