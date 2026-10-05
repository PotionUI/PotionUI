// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { tick } from 'svelte';

vi.mock('../../src/lib/utils/chipParser', () => ({
	hydrateSegments: async (segments: unknown[]) => segments
}));

const { default: SegmentedPromptEditor } = await import(
	'../../src/lib/components/SegmentedPromptEditor.svelte'
);
const { fitToolbar } = await import('../../src/lib/utils/toolbarFit');
const { createClassComponent } = await import('svelte/legacy');

const SLOT_WIDTH = 90;
const OTHER_WIDTH = 60;
let headerWidth = 1000;
let observers: Array<() => void> = [];

class TestResizeObserver {
	constructor(private cb: (entries: unknown[]) => void) {}
	observe(el: Element) {
		if (el.classList.contains('composer-toolbar')) observers.push(() => this.cb([]));
	}
	unobserve() {}
	disconnect() {}
}

beforeEach(() => {
	headerWidth = 1000;
	observers = [];
	vi.stubGlobal('ResizeObserver', TestResizeObserver);
	Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
		configurable: true,
		get(this: HTMLElement) {
			if (this.hasAttribute('data-toolbar-action')) return SLOT_WIDTH;
			if (this.parentElement?.classList.contains('composer-toolbar')) return OTHER_WIDTH;
			return 0;
		}
	});
	Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
		configurable: true,
		get(this: HTMLElement) {
			return this.classList.contains('composer-toolbar') ? headerWidth : 0;
		}
	});
});

afterEach(() => {
	document.body.innerHTML = '';
	vi.unstubAllGlobals();
	delete (HTMLElement.prototype as unknown as Record<string, unknown>).offsetWidth;
	delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientWidth;
});

function mount(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({
		component: SegmentedPromptEditor as never,
		target,
		props: {
			segments: [{ id: 'a', content: 'a lighthouse keeper', type: 'content', chips: {}, enabled: true }],
			onOpenStyles: () => {},
			onOpenVariableManager: () => {},
			...props
		}
	});
	return target;
}

async function settle() {
	await tick();
	await Promise.resolve();
	await tick();
	await Promise.resolve();
	await tick();
}

async function resizeTo(width: number) {
	headerWidth = width;
	observers.forEach((cb) => cb());
	await settle();
}

const visible = (target: HTMLElement) =>
	Array.from(target.querySelectorAll('header.composer-toolbar:not(.negative-header) [data-toolbar-action]')).map(
		(el) => (el as HTMLElement).dataset.toolbarAction
	);

async function openMenu(target: HTMLElement) {
	const trigger = target.querySelector('button[aria-label="More prompt actions"]') as HTMLButtonElement;
	trigger.click();
	await settle();
	return Array.from(target.querySelectorAll('[role="menu"] [role="menuitem"]')).map((el) => (el.textContent || '').trim());
}

describe('prompt header overflow', () => {
	it('shows every action when the pane is wide', async () => {
		const target = mount();
		await settle();
		expect(visible(target)).toEqual(['styles', 'prompts', 'segments', 'templates', 'variables']);
		const items = await openMenu(target);
		expect(items).not.toContain('Templates');
	});

	it('moves trailing actions into the more menu as the pane narrows, keeping order', async () => {
		const target = mount();
		await settle();
		await resizeTo(560);
		const shown = visible(target);
		expect(shown).not.toContain('templates');
		expect(shown).toContain('variables');
		expect(shown).toEqual(['styles', 'prompts', 'segments', 'variables'].filter((k) => shown.includes(k)));
		const items = await openMenu(target);
		expect(items).toContain('Templates');
		expect(items.indexOf('Segments')).toBeLessThan(items.indexOf('Templates'));
	});

	it('leaves no action both cut off and unreachable at any width', async () => {
		const target = mount();
		await settle();
		for (const width of [900, 700, 560, 460, 360, 260, 160]) {
			await resizeTo(width);
			const shown = visible(target);
			const menu = await openMenu(target);
			const everything = ['Styles', 'Prompts', 'Segments', 'Templates', 'Variables'];
			for (const label of everything) {
				const key = label.toLowerCase();
				expect(shown.includes(key) || menu.some((m) => m.startsWith(label))).toBe(true);
			}
			const used = shown.length * (SLOT_WIDTH + 8);
			expect(used).toBeLessThanOrEqual(Math.max(width - OTHER_WIDTH * 2 - 60, 0) + 1);
			(target.querySelector('button[aria-label="More prompt actions"]') as HTMLButtonElement).click();
			await settle();
		}
	});

	it('restores actions when the pane widens again', async () => {
		const target = mount();
		await settle();
		await resizeTo(300);
		expect(visible(target).length).toBeLessThan(5);
		await resizeTo(1000);
		expect(visible(target)).toEqual(['styles', 'prompts', 'segments', 'templates', 'variables']);
	});

	it('runs a collapsed action from the menu', async () => {
		const onOpenVariableManager = vi.fn();
		const target = mount({ onOpenVariableManager });
		await settle();
		await resizeTo(120);
		const trigger = target.querySelector('button[aria-label="More prompt actions"]') as HTMLButtonElement;
		trigger.click();
		await settle();
		const item = Array.from(target.querySelectorAll('[role="menuitem"]')).find((el) =>
			(el.textContent || '').trim().startsWith('Variables')
		) as HTMLButtonElement;
		item.click();
		expect(onOpenVariableManager).toHaveBeenCalledTimes(1);
	});

	it('collapses the negative header actions too', async () => {
		const target = mount({ negativeSegments: [{ id: 'n', content: 'blurry', type: 'content', chips: {}, enabled: true }] });
		await settle();
		await resizeTo(200);
		const negative = target.querySelector('header.negative-header') as HTMLElement;
		expect(negative.querySelectorAll('[data-toolbar-action]').length).toBeLessThan(3);
		const trigger = negative.querySelector('button[aria-label="More negative prompt actions"]') as HTMLButtonElement;
		trigger.click();
		await settle();
		expect(Array.from(negative.querySelectorAll('[role="menuitem"]')).map((e) => e.textContent?.trim())).toContain('Templates');
	});
});

describe('fitToolbar', () => {
	const base = { gap: 8, order: ['a', 'b', 'c'], collapseOrder: ['c', 'b', 'a'], widths: { a: 50, b: 50, c: 50 } };
	it('hides nothing when it fits', () => {
		expect(fitToolbar({ ...base, available: 500 })).toEqual([]);
	});
	it('hides in collapse order and reports in display order', () => {
		expect(fitToolbar({ ...base, available: 100 })).toEqual(['b', 'c']);
	});
	it('hides all when nothing fits', () => {
		expect(fitToolbar({ ...base, available: 10 })).toEqual(['a', 'b', 'c']);
	});
});
