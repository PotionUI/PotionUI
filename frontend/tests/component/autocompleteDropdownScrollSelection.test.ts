// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';

const { default: AutocompleteDropdown } = await import('$lib/components/AutocompleteDropdown.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { flushSync } = await import('svelte');

const ROW_HEIGHT = 48;
const VIEWPORT_HEIGHT = 200;

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

function suggestions(count: number) {
	return Array.from({ length: count }, (_, i) => ({
		id: `v${i}`,
		category_id: 'c1',
		label: `value ${i}`,
		value: `value ${i}`,
		sort_order: i,
		created_at: '',
		updated_at: ''
	}));
}

function stubLayout(scroller: HTMLElement, rows: HTMLElement[]) {
	Object.defineProperty(scroller, 'clientHeight', { value: VIEWPORT_HEIGHT, configurable: true });
	rows.forEach((row, i) => {
		Object.defineProperty(row, 'offsetTop', { value: i * ROW_HEIGHT, configurable: true });
		Object.defineProperty(row, 'offsetHeight', { value: ROW_HEIGHT, configurable: true });
	});
}

afterEach(() => {
	if (component) component.$destroy();
	component = undefined;
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('AutocompleteDropdown scroll selection', () => {
	it('never leaves the selected row scrolled behind the header on the way back to index 0', () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		const parentRef = document.createElement('div');
		parentRef.getBoundingClientRect = () =>
			({ top: 0, bottom: 0, left: 0, width: 0 }) as DOMRect;
		document.body.appendChild(parentRef);

		component = createClassComponent({
			component: AutocompleteDropdown as never,
			target,
			props: {
				categories: [],
				suggestions: suggestions(20),
				selectedIndex: 0,
				onSelectCategory: vi.fn(),
				onSelectValue: vi.fn(),
				currentPath: '',
				contextLabel: 'Phrasebook',
				parentRef,
				variant: 'segment-composer'
			}
		});
		flushSync();

		const header = document.querySelector('.picker-head');
		const scroller = document.querySelector<HTMLElement>('.picker-rows');
		expect(header).not.toBeNull();
		expect(scroller).not.toBeNull();
		expect(scroller!.contains(header)).toBe(false);

		const rows = Array.from(scroller!.querySelectorAll<HTMLElement>('.picker-row'));
		expect(rows).toHaveLength(20);
		stubLayout(scroller!, rows);
		scroller!.scrollTop = 0;

		for (let i = 1; i < 20; i++) {
			component.$set({ selectedIndex: i });
			flushSync();
		}
		expect(scroller!.scrollTop).toBeGreaterThan(0);

		component.$set({ selectedIndex: 0 });
		flushSync();

		expect(scroller!.scrollTop).toBe(0);
		expect(rows[0].offsetTop).toBeGreaterThanOrEqual(scroller!.scrollTop);
	});

	it('keeps the default-variant command palette header out of the scrolling list too (chat composer shares this variant)', () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		const parentRef = document.createElement('div');
		parentRef.getBoundingClientRect = () =>
			({ top: 0, bottom: 0, left: 0, width: 0 }) as DOMRect;
		document.body.appendChild(parentRef);

		component = createClassComponent({
			component: AutocompleteDropdown as never,
			target,
			props: {
				categories: [],
				suggestions: suggestions(20),
				selectedIndex: 0,
				onSelectCategory: vi.fn(),
				onSelectValue: vi.fn(),
				currentPath: 'lighting',
				contextLabel: 'Phrasebook',
				parentRef,
				variant: 'default'
			}
		});
		flushSync();

		const scroller = document.body.querySelector<HTMLElement>('[role="listbox"]');
		expect(scroller).not.toBeNull();
		const header = scroller!.previousElementSibling as HTMLElement | null;
		expect(header).not.toBeNull();
		expect(header!.textContent).toContain('lighting');
		expect(scroller!.contains(header)).toBe(false);

		const rows = Array.from(scroller!.querySelectorAll<HTMLElement>('[role="option"]'));
		expect(rows).toHaveLength(20);
		stubLayout(scroller!, rows);
		scroller!.scrollTop = 0;

		for (let i = 1; i < 20; i++) {
			component.$set({ selectedIndex: i });
			flushSync();
		}
		expect(scroller!.scrollTop).toBeGreaterThan(0);

		component.$set({ selectedIndex: 0 });
		flushSync();

		expect(scroller!.scrollTop).toBe(0);
		expect(rows[0].offsetTop).toBeGreaterThanOrEqual(scroller!.scrollTop);
	});
});
