import { describe, expect, it } from 'vitest';
import { get } from 'svelte/store';
import { targetActivatesOn } from '$lib/tool-tabs/frame/keymap';
import { settingsPaneWidth } from '$lib/stores/generationLayout';
import { viewportWidth } from '$lib/stores/viewport';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';

const { default: FrameHost } = await import('./FrameHost.svelte');

function press(key: string, init: KeyboardEventInit = {}, target: EventTarget = document.body): KeyboardEvent {
	const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
	target.dispatchEvent(event);
	flushSync();
	return event;
}

function render(props: Record<string, unknown> = {}) {
	const calls: string[] = [];
	const view = mountComponent<Record<string, unknown>>(FrameHost, { log: (entry: string) => calls.push(entry), ...props });
	const q = <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel);
	return { calls, view, q };
}

describe('EditorFrame layout', () => {
	it('lays out toolbar, rail, canvas and a left panel sized like the settings pane with options above the stack on desktop', () => {
		const { q } = render();
		const frame = q('[data-editor-frame="desktop"]')!;
		const panel = q<HTMLElement>('[data-frame-panel]')!;
		expect(frame.querySelector('[data-frame-column]')!.firstElementChild?.hasAttribute('data-host-toolbar')).toBe(true);
		expect(panel.style.width).toBe(`${settingsPaneWidth(get(viewportWidth))}px`);
		expect(panel.children[0].hasAttribute('data-host-options')).toBe(true);
		expect(q('[data-host-stack]')?.getAttribute('data-host-stack')).toBe('panel');
		expect(q('[data-frame-edits]')).toBeNull();
	});

	function desktopOrder(q: <T extends Element = HTMLElement>(sel: string) => T | null) {
		const row = q('[data-frame-panel]')!.parentElement!;
		return Array.from(row.children).map((el) => (el.hasAttribute('data-host-rail') ? 'rail' : el.hasAttribute('data-frame-panel') ? 'panel' : 'canvas'));
	}

	it('puts the panel between the rail and the canvas by default, with one line on its canvas edge', () => {
		const { q } = render();
		const panel = q<HTMLElement>('[data-frame-panel]')!;
		expect(desktopOrder(q)).toEqual(['rail', 'panel', 'canvas']);
		expect(panel.getAttribute('data-frame-panel')).toBe('left');
		expect(panel.classList.contains('border-r')).toBe(true);
		expect(panel.classList.contains('border-l')).toBe(false);
		expect(panel.classList.contains('border-line')).toBe(true);
	});

	it('puts the panel after the canvas when panelSide is right, with one line on its canvas edge', () => {
		const { q } = render({ panelSide: 'right' });
		const panel = q<HTMLElement>('[data-frame-panel]')!;
		expect(desktopOrder(q)).toEqual(['rail', 'canvas', 'panel']);
		expect(panel.getAttribute('data-frame-panel')).toBe('right');
		expect(panel.classList.contains('border-l')).toBe(true);
		expect(panel.classList.contains('border-r')).toBe(false);
		expect(panel.classList.contains('border-line')).toBe(true);
	});

	it.each([{}, { panelSide: 'right' }])('has no handle strip between the panel and the canvas, and the panel stays open: %j', (props) => {
		const { q } = render(props);
		expect(q('[data-frame-panel-handle]')).toBeNull();
		expect(q('[data-frame-panel]')!.classList.contains('hidden')).toBe(false);
	});

	it('makes the rail and a left panel one unit: a 2px line on the right edge of the rail, a strong outer divider, the selected tool keeping its selected look inside the line', () => {
		const { q } = render();
		const rail = q<HTMLElement>('[data-tool-rail]')!;
		const selected = q<HTMLElement>('button[data-tool="crop"]')!;
		const other = q<HTMLElement>('button[data-tool="rotate"]')!;
		expect(rail.hasAttribute('data-rail-joined')).toBe(true);
		expect(rail.classList.contains('border-r-2')).toBe(true);
		expect(rail.classList.contains('border-line')).toBe(true);
		expect(rail.classList.contains('border-l')).toBe(false);
		expect(rail.classList.contains('bg-surface-1/50')).toBe(true);
		expect(selected.classList.contains('bg-surface-1')).toBe(true);
		expect(selected.classList.contains('rounded-r-none')).toBe(false);
		expect(selected.classList.contains('w-11')).toBe(true);
		for (const cls of ['pl-1.5', 'pr-0', 'pr-1.5', 'ml-auto', 'mr-0', '-mr-px', 'rounded-r-none']) expect(rail.classList.contains(cls) || selected.classList.contains(cls)).toBe(false);
		expect(rail.classList.contains('px-[3px]')).toBe(true);
		expect(other.classList.contains('w-11')).toBe(true);
		expect(rail.style.width).toBe('52px');
		expect(3 + 44 + 3 + 2).toBe(52);
		expect(other.classList.contains('bg-surface-1')).toBe(false);
	});

	it('keeps the plain bordered rail when the panel is on the right', () => {
		const { q } = render({ panelSide: 'right' });
		const rail = q<HTMLElement>('[data-tool-rail]')!;
		expect(rail.hasAttribute('data-rail-joined')).toBe(false);
		expect(rail.classList.contains('border-r')).toBe(true);
		expect(rail.classList.contains('border-l')).toBe(false);
	});

	it.each([{}, { panelSide: 'right' }])('puts a purely visual divider the full height before the toolbar and the rail: %j', (props) => {
		const { q } = render(props);
		const frame = q('[data-editor-frame="desktop"]')!;
		const divider = frame.firstElementChild as HTMLElement;
		expect(divider.hasAttribute('data-frame-divider')).toBe(true);
		for (const cls of ['w-3', 'h-full', 'flex-shrink-0', 'border-r', 'border-line', 'bg-surface-3']) expect(divider.classList.contains(cls)).toBe(true);
		expect(divider.getAttribute('aria-hidden')).toBe('true');
		expect(divider.tagName).toBe('DIV');
		expect(divider.children).toHaveLength(0);
		expect(divider.getAttribute('tabindex')).toBeNull();
		const column = frame.children[1] as HTMLElement;
		expect(column.firstElementChild!.hasAttribute('data-host-toolbar')).toBe(true);
		expect(column.contains(q('[data-host-rail]'))).toBe(true);
		expect(frame.querySelectorAll('[data-frame-rail-handle]')).toHaveLength(0);
	});

	it('docks the options above the tool strip on a phone and opens the stack as a sheet', () => {
		const { q } = render({ phone: true });
		const frame = q('[data-editor-frame="phone"]')!;
		const order = Array.from(frame.children).map((el) => el.getAttribute('data-host-options') !== null ? 'options' : el.hasAttribute('data-host-rail') ? 'rail' : el.tagName);
		expect(order.slice(-2)).toEqual(['options', 'rail']);
		expect(q('[data-host-stack]')).toBeNull();
		const chip = q<HTMLButtonElement>('[data-frame-edits]')!;
		expect(chip.textContent?.replace(/\s+/g, ' ').trim()).toBe('2 edits');
		chip.click();
		flushSync();
		const sheet = q('[data-frame-sheet] [role="dialog"]')!;
		expect(sheet.getAttribute('aria-label')).toBe('Edits');
		expect(sheet.querySelector('[data-host-stack]')?.getAttribute('data-host-stack')).toBe('sheet');
		press('Escape');
		expect(q('[data-frame-sheet]')).toBeNull();
	});

	it('shows before while Hold is pressed on a phone', () => {
		const { q } = render({ phone: true });
		const hold = q<HTMLButtonElement>('[data-frame-hold]')!;
		hold.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
		flushSync();
		expect(hold.getAttribute('aria-pressed')).toBe('true');
		expect(q('[data-canvas-divider]')).not.toBeNull();
		hold.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
		flushSync();
		expect(hold.getAttribute('aria-pressed')).toBe('false');
	});

	it('hides Hold when there is nothing to compare', () => {
		const { q } = render({ phone: true, canCompare: false });
		expect(q('[data-frame-hold]')).toBeNull();
	});
});

describe('EditorFrame dock', () => {
	it('has no dock region unless the tool supplies one', () => {
		const { q } = render();
		expect(q('[data-frame-dock]')).toBeNull();
	});

	it('docks the region under the canvas in the canvas column, below the zoom and status chrome, not under the panel', () => {
		const { q } = render({ withDock: true });
		const dock = q('[data-frame-dock]')!;
		const column = dock.parentElement!;
		expect(dock.querySelector('[data-host-dock]')).not.toBeNull();
		expect(column.contains(q('[data-canvas-zoom]'))).toBe(true);
		expect(q('[data-frame-panel]')!.contains(dock)).toBe(false);
		const kids = Array.from(column.children);
		expect(kids.indexOf(dock)).toBe(kids.length - 1);
		expect(kids.find((child) => child.contains(q('[data-canvas-zoom]')))).not.toBe(dock);
		expect(dock.previousElementSibling?.contains(q('[data-canvas-zoom]'))).toBe(true);
	});

	it('is not drawn on a phone, where the prompt stays a sheet', () => {
		const { q } = render({ withDock: true, phone: true });
		expect(q('[data-frame-dock]')).toBeNull();
	});

	it('leaves the canvas one resize to refit when the dock appears, and refits once', () => {
		const callbacks: Array<() => void> = [];
		const original = globalThis.ResizeObserver;
		globalThis.ResizeObserver = class {
			constructor(callback: () => void) {
				callbacks.push(callback);
			}
			observe() {}
			unobserve() {}
			disconnect() {}
		} as never;
		let height = 300;
		const widthSpy = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth');
		const heightSpy = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight');
		Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 1000 });
		Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => height });
		try {
			const { q } = render();
			const before = q('[data-canvas-zoom]')?.closest('[data-zoom]')?.getAttribute('data-zoom');
			height = 180;
			const calls = callbacks.length;
			callbacks.forEach((callback) => callback());
			flushSync();
			const after = q('[data-canvas-zoom]')?.closest('[data-zoom]')?.getAttribute('data-zoom');
			expect(callbacks.length).toBe(calls);
			expect(Number(after)).toBeLessThan(Number(before));
		} finally {
			globalThis.ResizeObserver = original;
			if (widthSpy) Object.defineProperty(HTMLElement.prototype, 'clientWidth', widthSpy);
			if (heightSpy) Object.defineProperty(HTMLElement.prototype, 'clientHeight', heightSpy);
		}
	});
});

describe('EditorFrame keymap', () => {
	it('runs undo, redo and save and swallows the browser default', () => {
		const { calls } = render();
		const undo = press('z', { ctrlKey: true });
		press('Z', { ctrlKey: true, shiftKey: true });
		const save = press('s', { metaKey: true });
		expect(calls).toEqual(['undo', 'redo', 'save']);
		expect(undo.defaultPrevented).toBe(true);
		expect(save.defaultPrevented).toBe(true);
	});

	it('toggles before/after with backslash', () => {
		const { q } = render();
		press('\\');
		expect(q('[data-canvas-divider]')).not.toBeNull();
		press('\\');
		expect(q('[data-canvas-divider]')).toBeNull();
	});

	it('applies on Enter, but leaves Enter to a focused button', () => {
		const { calls, q } = render();
		press('Enter');
		const button = q<HTMLButtonElement>('[data-host-button]')!;
		button.focus();
		const onButton = press('Enter', {}, button);
		expect(calls).toEqual(['apply']);
		expect(onButton.defaultPrevented).toBe(false);
	});

	it('does nothing on Enter when the tool has no apply', () => {
		const { calls } = render({ withApply: false });
		expect(press('Enter').defaultPrevented).toBe(false);
		expect(calls).toEqual([]);
	});

	it('leaves keys alone while typing in a field', () => {
		const { calls, q } = render();
		const input = q<HTMLInputElement>('[data-host-input]')!;
		input.focus();
		const event = press('z', { ctrlKey: true }, input);
		press('\\', {}, input);
		expect(calls).toEqual([]);
		expect(event.defaultPrevented).toBe(false);
		expect(q('[data-canvas-divider]')).toBeNull();
	});

	it('is inert while the tab is not active', () => {
		const { calls } = render({ active: false });
		press('z', { ctrlKey: true });
		press('s', { ctrlKey: true });
		expect(calls).toEqual([]);
	});

	it('leaves the generate-page letter shortcuts and Ctrl Enter alone', () => {
		const { calls } = render();
		for (const key of ['g', 't', 'x', 's']) expect(press(key).defaultPrevented, key).toBe(false);
		expect(press('Enter', { ctrlKey: true }).defaultPrevented).toBe(false);
		expect(calls).toEqual([]);
	});

	it('holds Space for panning and releases it on keyup', () => {
		const { q } = render();
		const canvas = q('[data-editor-canvas]')!;
		press(' ');
		expect(canvas.className).toContain('cursor-grab');
		window.dispatchEvent(new KeyboardEvent('keyup', { key: ' ' }));
		flushSync();
		expect(canvas.className).not.toContain('cursor-grab');
	});

	it('stops listening once the sheet is open', () => {
		const { calls, q } = render({ phone: true });
		q<HTMLButtonElement>('[data-frame-edits]')!.click();
		flushSync();
		press('z', { ctrlKey: true });
		expect(calls).toEqual([]);
	});

	it('leaves Space and Enter to a focused button or native checkbox', () => {
		const { calls, q } = render();
		const button = q<HTMLButtonElement>('[data-host-button]')!;
		const box = q<HTMLInputElement>('[data-host-checkbox]')!;
		const canvas = q('[data-editor-canvas]')!;
		for (const target of [button, box]) {
			target.focus();
			expect(press(' ', {}, target).defaultPrevented).toBe(false);
			expect(press('Enter', {}, target).defaultPrevented).toBe(false);
		}
		expect(canvas.className).not.toContain('cursor-grab');
		expect(calls).toEqual([]);
	});

	it('drops pan mode when the tab stops being active while Space is held', () => {
		const { q, view } = render();
		press(' ');
		expect(q('[data-editor-canvas]')!.className).toContain('cursor-grab');
		view.rerender({ active: false });
		expect(q('[data-editor-canvas]')!.className).not.toContain('cursor-grab');
	});

	it('removes its window listeners when unmounted', () => {
		const { calls, view } = render();
		view.destroy();
		const event = press('z', { ctrlKey: true });
		expect(calls).toEqual([]);
		expect(event.defaultPrevented).toBe(false);
	});
});

describe('targetActivatesOn', () => {
	const cases: Array<[string, boolean]> = [
		['<button type="button">x</button>', true],
		['<a href="/x">x</a>', true],
		['<a>x</a>', false],
		['<select><option>x</option></select>', true],
		['<summary>x</summary>', true],
		['<input type="checkbox">', true],
		['<input type="radio">', true],
		['<input type="button">', true],
		['<input type="submit">', true],
		['<input type="reset">', true],
		['<input type="file">', true],
		['<input type="color">', true],
		['<input type="text">', false],
		['<input type="range">', false],
		['<div role="button">x</div>', true],
		['<div role="menuitem">x</div>', true],
		['<div role="tab">x</div>', true],
		['<div role="switch">x</div>', true],
		['<div>x</div>', false]
	];

	it.each(cases)('%s activates on its own key: %s', (html, expected) => {
		const host = document.createElement('div');
		host.innerHTML = html;
		expect(targetActivatesOn(host.firstElementChild)).toBe(expected);
	});

	it('looks through to the control around a nested target and ignores non-elements', () => {
		const host = document.createElement('div');
		host.innerHTML = '<button><span>x</span></button>';
		expect(targetActivatesOn(host.querySelector('span'))).toBe(true);
		expect(targetActivatesOn(null)).toBe(false);
		expect(targetActivatesOn(window)).toBe(false);
	});
});
