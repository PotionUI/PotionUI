import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { installFakeCanvas } from '../helpers/fakeCanvas';
import { mountComponent } from '../helpers/mount';
import { resetFailures } from '$lib/editors/guard';

vi.mock('$lib/services/api/index', () => ({
	api: { listFilters: async () => ({ filters: [], ops: [], groups: [] }) }
}));

const { default: PaintOptions } = await import('$lib/components/tool-tabs/image/PaintOptions.svelte');
const { PaintSession } = await import('$lib/paint/session');
const { createImageEditorApi } = await import('$lib/paint/registries');

const plugin = createImageEditorApi();
const CORE = ['Move', 'Brush', 'Eraser', 'Fill', 'Pick', 'Rectangle', 'Lasso'];

let restore: () => void;
let session: InstanceType<typeof PaintSession>;
const offs: Array<() => void> = [];

function toolLabels(): string[] {
	const bar = document.body.querySelector('[data-paint-tools]')!;
	return Array.from(bar.querySelectorAll('button')).map((button) => button.getAttribute('aria-label') || '');
}

function mountOptions() {
	const view = mountComponent(PaintOptions as never, { session, state: session.snapshot() });
	session.subscribe(() => view.rerender({ state: session.snapshot() }));
	flushSync();
	return view;
}

beforeEach(() => {
	restore = installFakeCanvas();
	resetFailures();
	session = new PaintSession();
	session.openBlank({ width: 32, height: 32, background: 'white', pen: null });
});

afterEach(() => {
	offs.splice(0).forEach((off) => off());
	session.detach();
	document.body.innerHTML = '';
	restore();
});

describe('paint options with plugin entries', () => {
	it('lists the core tools first and a plugin tool after them', () => {
		mountOptions();
		const core = toolLabels();
		offs.push(plugin.registerTool({ id: 'zigzag', label: 'Zigzag', icon: 'M0 0', group: 'shapes', pointerDown() {} }));
		flushSync();
		const after = toolLabels();
		expect(after.slice(0, core.length)).toEqual(core);
		expect(after.at(-1)).toBe('Zigzag');
		expect(core.length).toBe(CORE.length);
	});

	it('selects a plugin tool and removes it when unregistered', () => {
		mountOptions();
		const off = plugin.registerTool({ id: 'zigzag', label: 'Zigzag', icon: 'M0 0', group: 'shapes', pointerDown() {} });
		flushSync();
		document.body.querySelector<HTMLButtonElement>('[aria-label="Zigzag"]')!.click();
		expect(session.snapshot().toolId).toBe('zigzag');
		off();
		flushSync();
		expect(toolLabels()).not.toContain('Zigzag');
	});

	it('shows registered actions and runs them with the session', () => {
		mountOptions();
		const run = vi.fn();
		offs.push(plugin.registerAction({ id: 'stamp', label: 'Stamp', run }));
		flushSync();
		const button = Array.from(document.body.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Stamp')!;
		button.click();
		expect(run).toHaveBeenCalledWith(session);
	});

	it('survives an action that throws or rejects and keeps the others usable', async () => {
		mountOptions();
		const fine = vi.fn();
		offs.push(
			plugin.registerAction({
				id: 'bad',
				label: 'Bad',
				run() {
					throw new Error('plugin bug');
				}
			}),
			plugin.registerAction({ id: 'late', label: 'Late', run: async () => Promise.reject(new Error('later')) }),
			plugin.registerAction({ id: 'fine', label: 'Fine', run: fine })
		);
		flushSync();
		const find = (text: string) => Array.from(document.body.querySelectorAll('button')).find((b) => b.textContent?.trim() === text)!;
		expect(() => find('Bad').click()).not.toThrow();
		expect(() => find('Late').click()).not.toThrow();
		await Promise.resolve();
		find('Fine').click();
		expect(fine).toHaveBeenCalledTimes(1);
		expect(document.body.querySelector('[data-paint-tools]')).not.toBeNull();
	});

	it('keeps the session working when a plugin tool throws on pointer events', () => {
		mountOptions();
		offs.push(
			plugin.registerTool({
				id: 'boom',
				label: 'Boom',
				icon: 'M0 0',
				group: 'shapes',
				pointerDown() {
					throw new Error('down');
				},
				pointerMove() {
					throw new Error('move');
				},
				pointerUp() {
					throw new Error('up');
				}
			})
		);
		session.setTool('boom');
		const stage = document.createElement('canvas');
		const overlay = document.createElement('canvas');
		session.attach(stage, overlay);
		session.resize(400, 300, 1);
		const event = (type: string) => new PointerEvent(type, { button: 0, pointerId: 1, clientX: 200, clientY: 150 });
		expect(() => {
			session.onPointerDown(event('pointerdown'));
			session.onPointerMove(event('pointermove'));
			session.onPointerUp(event('pointerup'));
		}).not.toThrow();
		session.setTool('brush');
		expect(session.snapshot().toolId).toBe('brush');
	});
});
