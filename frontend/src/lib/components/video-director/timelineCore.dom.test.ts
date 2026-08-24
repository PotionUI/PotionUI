// @vitest-environment jsdom
// attachDrag needs `window` — split out of timelineCore.test.ts (node env)
// so the rest of that suite stays on the faster default environment.
import { describe, it, expect, vi } from 'vitest';
import { attachDrag } from './timelineCore';

function dispatchMouseEvent(type: string): void {
	window.dispatchEvent(new MouseEvent(type));
}

describe('attachDrag', () => {
	it('forwards mousemove to onMove while attached', () => {
		const onMove = vi.fn();
		attachDrag(onMove);

		dispatchMouseEvent('mousemove');
		dispatchMouseEvent('mousemove');
		expect(onMove).toHaveBeenCalledTimes(2);

		dispatchMouseEvent('mouseup');
	});

	it('detaches on mouseup: no further onMove calls, and onUp fires once', () => {
		const onMove = vi.fn();
		const onUp = vi.fn();
		attachDrag(onMove, onUp);

		dispatchMouseEvent('mousemove');
		dispatchMouseEvent('mouseup');
		expect(onUp).toHaveBeenCalledTimes(1);

		dispatchMouseEvent('mousemove');
		expect(onMove).toHaveBeenCalledTimes(1);

		dispatchMouseEvent('mouseup');
		expect(onUp).toHaveBeenCalledTimes(1);
	});

	it('runs independently per attach call (no cross-talk between two drags)', () => {
		const onMoveA = vi.fn();
		const onMoveB = vi.fn();
		attachDrag(onMoveA);
		dispatchMouseEvent('mouseup');
		attachDrag(onMoveB);

		dispatchMouseEvent('mousemove');
		expect(onMoveA).toHaveBeenCalledTimes(0);
		expect(onMoveB).toHaveBeenCalledTimes(1);

		dispatchMouseEvent('mouseup');
	});
});
