import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { installFakeCanvas } from '../helpers/fakeCanvas';
import { menuContext } from '../helpers/menuContext';
import { resetFailures } from '$lib/editors/guard';

vi.mock('$lib/services/api/index', () => ({
	api: { listFilters: async () => ({ filters: [], ops: [], groups: [] }) }
}));

const { PaintSession } = await import('$lib/paint/session');
const { createImageEditorApi } = await import('$lib/paint/registries');
const { imageRail } = await import('$lib/tool-tabs/image/steps');
const { buildMenu } = await import('$lib/editors/menuItems');
const { ensureCoreEditorMenu } = await import('$lib/editors/coreMenu');
const { railTools } = await import('$lib/editors/railTools');

const plugin = createImageEditorApi();

let restore: () => void;
let session: InstanceType<typeof PaintSession>;
const offs: Array<() => void> = [];

const paintGroup = () => imageRail().find((group) => group.id === 'paint')!;

beforeEach(() => {
	restore = installFakeCanvas();
	resetFailures();
	ensureCoreEditorMenu();
	session = new PaintSession();
	session.openBlank({ width: 32, height: 32, background: 'white', pen: null });
});

afterEach(() => {
	offs.splice(0).forEach((off) => off());
	session.detach();
	document.body.innerHTML = '';
	restore();
});

describe('paint entries with plugin registrations', () => {
	it('lists the core paint entries first and a plugin tool after them, not marked as a plugin', () => {
		const core = paintGroup().tools.map((tool) => tool.label);
		expect(core).toEqual(['Brush', 'Eraser', 'Fill', 'Eyedropper']);
		offs.push(plugin.registerTool({ id: 'zigzag', label: 'Zigzag', icon: 'M0 0', group: 'shapes', pointerDown() {} }));
		flushSync();
		expect(paintGroup().tools.map((tool) => tool.label)).toEqual([...core, 'Zigzag']);
		expect(railTools.get('image', 'paint.zigzag')?.pluginId).toBeUndefined();
	});

	it('puts a plugin tool that names the select group in Select and move and removes it again', () => {
		const off = plugin.registerTool({ id: 'wand', label: 'Wand', icon: 'M0 0', group: 'select', pointerDown() {} });
		expect(imageRail().find((group) => group.id === 'select')!.tools.map((tool) => tool.label)).toContain('Wand');
		off();
		expect(imageRail().find((group) => group.id === 'select')!.tools.map((tool) => tool.label)).not.toContain('Wand');
		expect(session.getTool('wand')).toBeUndefined();
	});

	it('selects a plugin tool through the session', () => {
		offs.push(plugin.registerTool({ id: 'zigzag', label: 'Zigzag', icon: 'M0 0', group: 'shapes', pointerDown() {} }));
		session.setTool('zigzag');
		expect(session.snapshot().toolId).toBe('zigzag');
	});

	it('offers registered actions in the Edit menu while a paint entry is selected and runs them with the session', () => {
		const run = vi.fn();
		offs.push(plugin.registerAction({ id: 'stamp', label: 'Stamp', run }));
		const rows = (tool: string) => buildMenu('edit', menuContext([], { tool })).groups.flat();
		const row = rows('paint.brush').find((entry) => entry.label === 'Stamp')!;
		expect(row).toBeDefined();
		expect(rows('crop').some((entry) => entry.label === 'Stamp')).toBe(false);
		row.run();
		expect(run).toHaveBeenCalledWith(session);
	});

	it('survives an action that throws or rejects and keeps the others usable', async () => {
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
		const find = (text: string) => buildMenu('edit', menuContext([], { tool: 'paint.brush' })).groups.flat().find((entry) => entry.label === text)!;
		expect(() => find('Bad').run()).not.toThrow();
		expect(() => find('Late').run()).not.toThrow();
		await Promise.resolve();
		find('Fine').run();
		expect(fine).toHaveBeenCalledTimes(1);
	});

	it('keeps the session working when a plugin tool throws on pointer events', () => {
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
