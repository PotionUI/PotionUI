import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { railContentHeight, type FoldInput } from '$lib/editors/railFold';

const { default: ToolRail } = await import('$lib/components/tool-tabs/frame/ToolRail.svelte');

const mk = (id: string, label = id) => ({ id, label, icon: 'extension' });

const groups = [
	{ id: 'shape', label: 'Transform', fold: 4, tools: ['crop', 'resize', 'canvas', 'slice'].map((id) => mk(id)) },
	{ id: 'paint', label: 'Paint', tools: [mk('paint')] },
	{ id: 'generate', label: 'Generate', fold: 5, tools: [mk('edit_region'), mk('remove_object')] },
	{ id: 'look', label: 'Light and colour', fold: 2, tools: ['light', 'colour', 'split_tone', 'curves', 'lut'].map((id) => mk(id)) },
	{ id: 'fx', label: 'Effects', fold: 1, tools: ['film_grain', 'vignette', 'grayscale', 'invert'].map((id) => mk(id)) },
	{ id: 'looks', label: 'Looks', tools: [mk('effects', 'Looks')] },
	{ id: 'tools', label: 'Cut out', fold: 3, tools: [mk('colorKey'), mk('subject')] }
];

const inputs: FoldInput[] = groups.map((group) => ({ id: group.id, count: group.tools.length, fold: group.fold }));
const FULL = railContentHeight(inputs, new Set());

let height = 5000;
let observers: Array<() => void> = [];

beforeEach(() => {
	height = 5000;
	observers = [];
	localStorage.clear();
	Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
		configurable: true,
		get() {
			return (this as HTMLElement).dataset.toolRail === 'icons' ? height : 0;
		}
	});
	vi.stubGlobal(
		'ResizeObserver',
		class {
			constructor(callback: () => void) {
				observers.push(callback);
			}
			observe() {}
			unobserve() {}
			disconnect() {}
		}
	);
});

afterEach(() => {
	vi.useRealTimers();
	vi.unstubAllGlobals();
	document.body.innerHTML = '';
	Reflect.deleteProperty(HTMLElement.prototype, 'clientHeight');
});

function rail(props: Record<string, unknown> = {}) {
	const picked: string[] = [];
	const view = mountComponent(ToolRail, { groups, active: 'crop', variant: 'icons', memoryKey: 'image', onselect: (id: string) => picked.push(id), ...props });
	const root = () => view.target.querySelector<HTMLElement>('[data-tool-rail]')!;
	const resize = (next: number) => {
		height = next;
		observers.forEach((callback) => callback());
		flushSync();
	};
	const entries = () => Array.from(root().querySelectorAll('button[data-tool]')).map((button) => (button as HTMLElement).dataset.tool);
	const folded = () => root().dataset.railFolded ?? '';
	const flyout = () => document.body.querySelector<HTMLElement>('[data-rail-flyout]');
	return { view, picked, root, resize, entries, folded, flyout };
}

describe('ToolRail folding', () => {
	it('shows every entry as its own icon with a divider between every group when the rail fits', () => {
		const { root, entries, folded } = rail();
		expect(entries()).toHaveLength(19);
		expect(root().querySelectorAll('[role="separator"]')).toHaveLength(6);
		expect(root().querySelector('[data-group-button]')).toBeNull();
		expect(folded()).toBe('');
		expect(document.body.querySelector('[data-rail-flyout]')).toBeNull();
	});

	it('folds Effects first when the rail is a little too short, and never shows a scrollbar', () => {
		const { root, resize, entries, folded } = rail();
		resize(FULL - 1);
		expect(folded()).toBe('fx');
		expect(entries()).toHaveLength(15);
		expect(root().querySelector('[data-group-button="fx"]')).not.toBeNull();
		expect(root().className).toContain('overflow-hidden');
	});

	it('folds Effects, Light and colour and Cut out at 720 high, in order, and leaves single-entry groups alone', () => {
		const { resize, folded, root } = rail();
		resize(720 - 118);
		expect(folded()).toBe('fx look tools');
		expect(root().querySelector('[data-group-button="paint"]')).toBeNull();
		expect(root().querySelector('[data-group-button="looks"]')).toBeNull();
		expect(root().querySelector('button[data-tool="paint"]')).not.toBeNull();
		resize(5000);
		expect(folded()).toBe('');
	});

	it('does not oscillate around the boundary height', () => {
		const { resize, folded } = rail();
		const seen = new Set<string>();
		for (let i = 0; i < 6; i++) {
			resize(FULL - 1);
			seen.add(folded());
			resize(FULL);
			seen.add(folded());
		}
		expect([...seen].sort()).toEqual(['', 'fx']);
		resize(FULL - 1);
		resize(FULL - 1);
		expect(folded()).toBe('fx');
	});

	it('keeps every icon where it is when the selection changes', () => {
		const { view, resize, root } = rail();
		const shape = () => Array.from(root().children).map((node) => `${node.tagName}:${(node as HTMLElement).dataset.tool ?? (node as HTMLElement).dataset.groupButton ?? ''}:${node.className.includes('h-10') ? 'icon' : 'other'}`);
		for (const height of [5000, 900 - 118, 720 - 118]) {
			resize(height);
			const before = shape();
			const foldedBefore = root().dataset.railFolded;
			for (const active of ['vignette', 'light', 'colorKey', 'edit_region', 'paint', 'effects']) {
				view.rerender({ active });
				flushSync();
				expect(shape()).toEqual(before);
				expect(root().dataset.railFolded).toBe(foldedBefore);
			}
		}
	});

	it('a folded group is one icon of the same size showing the selected entry, with a corner mark', () => {
		const { view, resize, root } = rail();
		resize(900 - 118);
		view.rerender({ active: 'vignette' });
		flushSync();
		const button = root().querySelector<HTMLElement>('[data-group-button="fx"]')!;
		expect(button.dataset.shown).toBe('vignette');
		expect(button.className).toContain('is-on');
		expect(button.className).toContain('h-10');
		expect(button.querySelector('.corner')).not.toBeNull();
	});

	it('marks applied entries with a dot on the icon, on the folded icon and in the flyout', () => {
		const { view, resize, root } = rail({ applied: ['light', 'film_grain'] });
		expect(root().querySelector('button[data-tool="light"] [data-tool-applied]')).not.toBeNull();
		expect(root().querySelector('button[data-tool="colour"] [data-tool-applied]')).toBeNull();
		resize(900 - 118);
		expect(root().querySelector('[data-group-button="fx"] [data-tool-applied]')).not.toBeNull();
		root().querySelector<HTMLButtonElement>('[data-group-button="fx"]')!.click();
		flushSync();
		expect(document.body.querySelector('[data-rail-flyout] [data-flyout-item="film_grain"] [data-flyout-applied]')).not.toBeNull();
		expect(document.body.querySelector('[data-rail-flyout] [data-flyout-item="vignette"] [data-flyout-applied]')).toBeNull();
		expect(view.target).toBeTruthy();
	});
});

describe('ToolRail flyout', () => {
	it('opens on click into a portal beside the folded icon, listing the group, and choosing an entry selects it and closes', async () => {
		const { root, resize, picked, flyout } = rail();
		resize(900 - 118);
		root().querySelector<HTMLButtonElement>('[data-group-button="fx"]')!.click();
		flushSync();
		expect(flyout()).not.toBeNull();
		expect(root().contains(flyout())).toBe(false);
		expect(flyout()!.parentElement).toBe(document.body);
		expect(Array.from(flyout()!.querySelectorAll('[data-flyout-item]')).map((item) => (item as HTMLElement).dataset.flyoutItem)).toEqual(['film_grain', 'vignette', 'grayscale', 'invert']);
		flyout()!.querySelector<HTMLButtonElement>('[data-flyout-item="grayscale"]')!.click();
		flushSync();
		expect(picked).toEqual(['grayscale']);
		expect(flyout()).toBeNull();
	});

	it('opens on hover after a short delay and closes when the pointer leaves', () => {
		vi.useFakeTimers();
		const { root, resize, flyout } = rail();
		resize(900 - 118);
		const button = root().querySelector<HTMLElement>('[data-group-button="fx"]')!;
		button.dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }));
		vi.advanceTimersByTime(100);
		flushSync();
		expect(flyout()).toBeNull();
		vi.advanceTimersByTime(200);
		flushSync();
		expect(flyout()).not.toBeNull();
		button.dispatchEvent(new PointerEvent('pointerleave', { pointerType: 'mouse' }));
		vi.advanceTimersByTime(300);
		flushSync();
		expect(flyout()).toBeNull();
	});

	it('a click on a group whose flyout opened by hover keeps it open, and a second click closes it', () => {
		vi.useFakeTimers();
		const { root, resize, flyout } = rail();
		resize(900 - 118);
		const button = root().querySelector<HTMLElement>('[data-group-button="fx"]')!;
		button.dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }));
		vi.advanceTimersByTime(300);
		flushSync();
		expect(flyout()).not.toBeNull();
		button.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }));
		flushSync();
		expect(flyout()).not.toBeNull();
		button.dispatchEvent(new PointerEvent('pointerleave', { pointerType: 'mouse' }));
		vi.advanceTimersByTime(500);
		flushSync();
		expect(flyout()).not.toBeNull();
		button.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }));
		flushSync();
		expect(flyout()).toBeNull();
	});

	it('works from the keyboard: Enter opens, arrows move, Enter picks, Escape closes and returns focus', async () => {
		const { root, resize, picked, flyout } = rail();
		resize(900 - 118);
		const button = root().querySelector<HTMLButtonElement>('[data-group-button="fx"]')!;
		button.focus();
		button.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		flushSync();
		await Promise.resolve();
		expect(flyout()).not.toBeNull();
		expect((document.activeElement as HTMLElement).dataset.flyoutItem).toBe('film_grain');
		flyout()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
		expect((document.activeElement as HTMLElement).dataset.flyoutItem).toBe('vignette');
		flyout()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
		expect((document.activeElement as HTMLElement).dataset.flyoutItem).toBe('invert');
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
		flushSync();
		expect(flyout()).toBeNull();
		expect(picked).toEqual([]);
		expect(document.activeElement).toBe(button);
		button.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		flushSync();
		await Promise.resolve();
		(document.activeElement as HTMLElement).click();
		flushSync();
		expect(picked).toEqual(['film_grain']);
	});

	it('remembers the entry last used in each group, and shows it when nothing in the group is selected', () => {
		const first = rail();
		first.resize(900 - 118);
		first.root().querySelector<HTMLButtonElement>('[data-group-button="fx"]')!.click();
		flushSync();
		first.flyout()!.querySelector<HTMLButtonElement>('[data-flyout-item="invert"]')!.click();
		flushSync();
		expect(first.root().querySelector<HTMLElement>('[data-group-button="fx"]')!.dataset.shown).toBe('invert');
		expect(JSON.parse(localStorage.getItem('potionui_editor_group_last')!)).toEqual({ image: { fx: 'invert', shape: 'crop' } });
		document.body.innerHTML = '';
		const second = rail({ active: 'paint' });
		second.resize(900 - 118);
		expect(second.root().querySelector<HTMLElement>('[data-group-button="fx"]')!.dataset.shown).toBe('invert');
	});
});

describe('ToolRail on a phone', () => {
	it('shows groups and opens a sheet of the entries for a multi-entry group, selecting directly for a single one', () => {
		const { view, picked } = rail({ variant: 'strip', groupedStrip: true });
		const strip = view.target.querySelector<HTMLElement>('[data-tool-rail="strip"]')!;
		expect(strip.querySelectorAll('[data-group-button]')).toHaveLength(5);
		expect(strip.querySelectorAll('button[data-tool]')).toHaveLength(2);
		strip.querySelector<HTMLButtonElement>('[data-group-button="fx"]')!.click();
		flushSync();
		const sheet = document.body.querySelector('[data-rail-sheet="fx"]')!;
		expect(Array.from(sheet.querySelectorAll('[data-sheet-item]')).map((item) => (item as HTMLElement).dataset.sheetItem)).toEqual(['film_grain', 'vignette', 'grayscale', 'invert']);
		(sheet.querySelector('[data-sheet-item="vignette"]') as HTMLButtonElement).click();
		flushSync();
		expect(picked).toEqual(['vignette']);
		expect(document.body.querySelector('[data-rail-sheet]')).toBeNull();
		strip.querySelector<HTMLButtonElement>('button[data-tool="paint"]')!.click();
		expect(picked).toEqual(['vignette', 'paint']);
	});
});
