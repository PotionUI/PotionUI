import { describe, expect, it } from 'vitest';
import { targetActivatesOn } from '$lib/tool-tabs/frame/keymap';
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
	it('lays out toolbar, rail, canvas and a 312px panel with options above the stack on desktop', () => {
		const { q } = render();
		const frame = q('[data-editor-frame="desktop"]')!;
		const panel = q('[data-frame-panel]')!;
		expect(frame.firstElementChild?.hasAttribute('data-host-toolbar')).toBe(true);
		expect(panel.className).toContain('w-[312px]');
		expect(panel.children[0].hasAttribute('data-host-options')).toBe(true);
		expect(q('[data-host-stack]')?.getAttribute('data-host-stack')).toBe('panel');
		expect(q('[data-frame-edits]')).toBeNull();
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
