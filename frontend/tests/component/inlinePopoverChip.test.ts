// @vitest-environment jsdom
//
// InlinePopoverChip's popover used to render `absolute`, inside the chip —
// clipped whenever an ancestor (PromptSegment.svelte's `.card`) sets
// `overflow: hidden`. This proves the portal fix: the popover is a real
// `<body>`-level, `position: fixed` node while open, a click inside it
// (through the portal, not through `chipRef`) doesn't dismiss it, and it's
// cleaned up on both close and unmount.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync, createRawSnippet } from 'svelte';

const { default: InlinePopoverChip } = await import('$lib/components/InlinePopoverChip.svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof mount>;

const labelSnippet = createRawSnippet(() => ({
	render: () => `<span>trigger label</span>`
}));

const popoverSnippet = createRawSnippet(() => ({
	render: () => `<button type="button" data-testid="inside-popover">inside</button>`
}));

function mountChip(onremove = vi.fn()) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(InlinePopoverChip, {
		target,
		props: {
			popoverLabel: 'Test popover',
			label: labelSnippet,
			popover: popoverSnippet,
			onremove
		}
	});
	flushSync();
}

function openChip() {
	const trigger = target.querySelectorAll('button')[0];
	trigger.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
	flushSync();
}

beforeEach(() => {
	if (!Element.prototype.animate) {
		Element.prototype.animate = () => {
			const animation = {
				cancel() {},
				finished: Promise.resolve(),
				onfinish: null as (() => void) | null,
				currentTime: 0,
				playbackRate: 1
			};
			setTimeout(() => animation.onfinish?.(), 0);
			return animation as never;
		};
	}
});

afterEach(() => {
	if (component) unmount(component);
	target?.remove();
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('InlinePopoverChip portal popover', () => {
	it('renders the open popover as a fixed, body-level node', () => {
		mountChip();
		openChip();

		const popover = document.querySelector('[role="dialog"]');
		expect(popover).not.toBeNull();
		expect(popover?.parentElement).toBe(document.body);
		expect(popover?.classList.contains('fixed')).toBe(true);
		// Not a descendant of the chip anymore — that's the whole point of the portal.
		expect(target.contains(popover)).toBe(false);
	});

	it('does not close on a pointerdown inside the portaled popover', () => {
		mountChip();
		openChip();

		const inside = document.querySelector('[data-testid="inside-popover"]');
		expect(inside).not.toBeNull();
		inside?.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }));
		flushSync();

		expect(document.querySelector('[role="dialog"]')).not.toBeNull();
	});

	it('closes on a pointerdown outside the chip and the popover', () => {
		mountChip();
		openChip();
		expect(document.querySelector('[role="dialog"]')).not.toBeNull();

		document.body.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }));
		flushSync();

		expect(document.querySelector('[role="dialog"]')).toBeNull();
	});

	it('closes on Escape', () => {
		mountChip();
		openChip();
		expect(document.querySelector('[role="dialog"]')).not.toBeNull();

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
		flushSync();

		expect(document.querySelector('[role="dialog"]')).toBeNull();
	});

	it('clamps a popover taller than the viewport room to that room and scrolls the rest', () => {
		// scrollHeight always reads 0 in jsdom's layout-less environment; stub it
		// so the popover measures as "tall content" the way a long option list
		// or long variable values would in a real browser.
		const scrollHeightSpy = vi.spyOn(Element.prototype, 'scrollHeight', 'get').mockReturnValue(900);
		Object.defineProperty(window, 'innerHeight', { value: 400, configurable: true });

		mountChip();
		const chip = target.querySelector('.inline-popover-chip') as HTMLElement;
		// Pinned low, with only 20px of room below and 350px above -- the
		// popover has to flip up, and 350px of room is far short of the 900px
		// of content, so it must clamp rather than run off the top.
		chip.getBoundingClientRect = () =>
			({ top: 350, bottom: 380, left: 200, right: 260, width: 60, height: 30, x: 200, y: 350, toJSON() {} }) as DOMRect;

		openChip();

		const popover = document.querySelector('[role="dialog"]') as HTMLElement;
		expect(popover).not.toBeNull();
		expect(popover.style.overflowY).toBe('auto');
		const maxHeight = parseFloat(popover.style.maxHeight);
		expect(maxHeight).toBeGreaterThan(0);
		// Room above the trigger is 350px -- the clamp must fit inside it.
		expect(maxHeight).toBeLessThanOrEqual(350);

		scrollHeightSpy.mockRestore();
	});

	it('removes the portaled popover from the DOM when the chip unmounts while open', () => {
		mountChip();
		openChip();
		expect(document.querySelector('[role="dialog"]')).not.toBeNull();

		unmount(component);
		component = undefined as unknown as ReturnType<typeof mount>;

		expect(document.querySelector('[role="dialog"]')).toBeNull();
	});
});
