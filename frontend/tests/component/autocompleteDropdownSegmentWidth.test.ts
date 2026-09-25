// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { AUTOCOMPLETE_SEGMENT_WIDTH } from '$lib/utils/autocompleteAnchor';

const { default: AutocompleteDropdown } = await import('$lib/components/AutocompleteDropdown.svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | undefined;
let parentRef: HTMLDivElement;

const WIDE_SEGMENT_RECT = { top: 260, bottom: 300, left: 40, right: 940, width: 900, height: 40 } as DOMRect;

function stubParent(): HTMLDivElement {
	const el = document.createElement('div');
	el.getBoundingClientRect = () => WIDE_SEGMENT_RECT;
	document.body.appendChild(el);
	return el;
}

function stubCaretSelection(left: number) {
	const range = {
		startContainer: parentRef,
		getBoundingClientRect: () => ({ top: 260, bottom: 280, left, height: 20 })
	};
	return { rangeCount: 1, getRangeAt: () => range } as unknown as Selection;
}

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	target?.remove();
	parentRef?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

function mountDropdown(caretLeft?: number) {
	target = document.createElement('div');
	document.body.appendChild(target);
	parentRef = stubParent();
	if (caretLeft !== undefined) {
		vi.spyOn(window, 'getSelection').mockReturnValue(stubCaretSelection(caretLeft));
	}
	component = mount(AutocompleteDropdown as never, {
		target,
		props: {
			categories: [],
			suggestions: [],
			selectedIndex: 0,
			onSelectCategory: () => {},
			onSelectValue: () => {},
			currentPath: '',
			contextLabel: 'References',
			parentRef,
			variant: 'segment-composer'
		}
	});
	flushSync();
}

describe('AutocompleteDropdown segment-composer width and caret anchoring', () => {
	it('never stretches to the wide segment box — width is the fixed 27rem picker width', () => {
		mountDropdown(500);

		const picker = document.querySelector<HTMLElement>('.floating.picker');
		expect(picker).not.toBeNull();
		expect(picker!.style.width).toBe(`${AUTOCOMPLETE_SEGMENT_WIDTH}px`);
		expect(picker!.style.width).not.toBe(`${WIDE_SEGMENT_RECT.width}px`);
	});

	it('left-anchors at the caret x rather than the segment box left', () => {
		mountDropdown(500);

		const picker = document.querySelector<HTMLElement>('.floating.picker');
		expect(picker!.style.left).toBe('500px');
		expect(picker!.style.left).not.toBe(`${WIDE_SEGMENT_RECT.left}px`);
	});

	it('flips left of the caret when the fixed width would overflow the viewport', () => {
		const overflowLeft = window.innerWidth - 40;
		mountDropdown(overflowLeft);

		const picker = document.querySelector<HTMLElement>('.floating.picker');
		const left = parseFloat(picker!.style.left);
		expect(left).toBeLessThan(overflowLeft);
	});
});
