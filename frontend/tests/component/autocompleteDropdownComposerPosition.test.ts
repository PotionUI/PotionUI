// @vitest-environment jsdom
//
// Two live bugs the maintainer hit in the segment composer:
// (1) clicking the segment footer's "Phrasebook"/"Variable" insert buttons
//     added the trigger character but never opened the picker — a caret left
//     just after the parent element (not inside the just-inserted text node)
//     failed detectPhrasebookTrigger's `startContainer` type check and closed
//     it right back. Covered in promptSegmentInsertTriggers.test.ts.
// (2) the composer-variant picker rendered far from the caret — the mock's
//     own `.picker`/`.floating` classes carry static prototype `left`/`top`,
//     which fought the real, caret-anchored position once both landed on the
//     same (or a wrapping) element. These prove the composer variant's
//     rendered position is driven by the exact same computeAutocompletePlacement
//     call as the default variant, for the same anchor rect.
import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { computeAutocompletePlacement } from '$lib/utils/autocompleteAnchor';

const { default: AutocompleteDropdown } = await import('$lib/components/AutocompleteDropdown.svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | undefined;
let parentRef: HTMLDivElement;

const ANCHOR_RECT = { top: 260, bottom: 300, left: 40, right: 340, width: 300, height: 40 } as DOMRect;

function stubParent(): HTMLDivElement {
	const el = document.createElement('div');
	el.getBoundingClientRect = () => ANCHOR_RECT;
	document.body.appendChild(el);
	return el;
}

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	target?.remove();
	parentRef?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

function mountDropdown(variant: 'default' | 'segment-composer') {
	target = document.createElement('div');
	document.body.appendChild(target);
	parentRef = stubParent();
	component = mount(AutocompleteDropdown as never, {
		target,
		props: {
			categories: [],
			suggestions: [
				{
					id: 'v1',
					category_id: 'c1',
					label: 'amber apothecary bottle',
					value: 'amber apothecary bottle',
					sort_order: 0,
					created_at: '',
					updated_at: ''
				}
			],
			selectedIndex: 0,
			onSelectCategory: () => {},
			onSelectValue: () => {},
			currentPath: 'object',
			contextLabel: 'Phrasebook',
			parentRef,
			variant
		}
	});
	flushSync();
}

describe('AutocompleteDropdown composer-variant position parity', () => {
	it('positions the composer-variant picker with the same computeAutocompletePlacement result as the default variant', () => {
		const expected = computeAutocompletePlacement(ANCHOR_RECT, {
			width: window.innerWidth,
			height: window.innerHeight
		});

		mountDropdown('segment-composer');
		const picker = document.querySelector<HTMLElement>('.floating.picker');
		expect(picker).not.toBeNull();
		expect(picker!.style.position).toBe('fixed');
		expect(picker!.style.left).toBe(`${expected.left}px`);
		if (expected.openAbove) {
			expect(picker!.style.bottom).toBe(`${expected.bottom}px`);
			expect(picker!.style.top).toBe('auto');
		} else {
			expect(picker!.style.top).toBe(`${expected.top}px`);
			expect(picker!.style.bottom).toBe('auto');
		}
		// The mock's own `.picker{left:52px;top:250px}` must never win.
		expect(picker!.style.left).not.toBe('52px');
		unmount(component!);
		component = undefined;
		target.remove();
		parentRef.remove();

		mountDropdown('default');
		const defaultDropdown = document.querySelector<HTMLElement>('.fixed.z-\\[99999\\]');
		expect(defaultDropdown).not.toBeNull();
		expect(defaultDropdown!.style.left).toBe(`${expected.left}px`);
	});

	it('wraps the composer-variant portal root in a non-positioning display:contents carrier', () => {
		mountDropdown('segment-composer');
		const scopeWrapper = document.querySelector<HTMLElement>('body > .segment-composer');
		expect(scopeWrapper).not.toBeNull();
		expect(scopeWrapper!.style.display).toBe('contents');
	});
});
