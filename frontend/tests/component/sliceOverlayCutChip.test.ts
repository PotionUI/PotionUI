// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import SliceOverlay from '$lib/components/tool-tabs/image/slice/SliceOverlay.svelte';
import type { Analysis } from '$lib/tool-tabs/image/slice/detect';
import type { Cell } from '$lib/tool-tabs/image/slice/model';

const busy: Analysis = { w: 10, h: 10, bg: [255, 255, 255], transparent: false, isBg: new Uint8Array(100), minGutter: 1 };

const cells: Cell[] = [
	{ id: 'a', x0: 0, y0: 0, x1: 0.48, y1: 1 },
	{ id: 'b', x0: 0.52, y0: 0, x1: 1, y1: 1 }
];

let cleanup: (() => void) | undefined;

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
});

function mountOverlay(onchange = vi.fn()) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = mount(SliceOverlay, {
		target,
		props: { cells, an: busy, size: { width: 1000, height: 1000 }, onchange, onselect: vi.fn() }
	});
	flushSync();
	cleanup = () => {
		unmount(instance);
		target.remove();
	};
	return target;
}

describe('the cut warning on a line through a panel', () => {
	it('lets pointer events reach the line handle beneath it', () => {
		const chip = mountOverlay().querySelector('[data-slice-cut]') as HTMLElement;
		expect(chip).toBeTruthy();
		expect(chip.className).toContain('pointer-events-none');
	});

	it('does not sit centred on the line', () => {
		const chip = mountOverlay().querySelector('[data-slice-cut]') as HTMLElement;
		expect(chip.className).not.toContain('-translate-x-1/2');
		expect(chip.style.left).toContain('calc(');
	});

	it('keeps the merge action clickable', () => {
		const onchange = vi.fn();
		const merge = mountOverlay(onchange).querySelector('[data-slice-cut-merge]') as HTMLElement;
		expect(merge.className).toContain('pointer-events-auto');
		merge.click();
		flushSync();
		expect(onchange).toHaveBeenCalled();
	});
});
