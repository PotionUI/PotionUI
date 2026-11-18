import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, tick, unmount } from 'svelte';
import { get } from 'svelte/store';
import { installPixelCanvas } from './helpers/pixelCanvas';
import type { CanvasAction } from '$lib/editors/railTools';

vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });

const { default: CanvasActionBar } = await import('$lib/components/tool-tabs/frame/CanvasActionBar.svelte');
const { default: EditorFrame } = await import('$lib/components/tool-tabs/frame/EditorFrame.svelte');
const { default: MaskLayer } = await import('$lib/components/tool-tabs/image/MaskLayer.svelte');
const { maskArea, resetMaskTool } = await import('$lib/tool-tabs/image/maskOverlays');
const { rasterizePolygon } = await import('$lib/paint/raster/selectionMask');

const instances: Array<() => void> = [];

function place<P extends Record<string, unknown>>(component: unknown, props: P) {
	const target = document.body.appendChild(document.createElement('div'));
	const instance = mount(component as never, { target, props }) as Record<string, unknown>;
	flushSync();
	instances.push(() => {
		unmount(instance);
		target.remove();
	});
	return { target, instance };
}

const act = (id: string, run: () => void = () => undefined, extra: Partial<CanvasAction> = {}): CanvasAction => ({ id, label: id.toUpperCase(), icon: 'check', kind: 'action', run, ...extra });
const button = (root: ParentNode, id: string) => root.querySelector<HTMLButtonElement>(`[data-canvas-action="${id}"]`)!;

beforeEach(() => {
	resetMaskTool();
});

afterEach(() => {
	instances.splice(0).forEach((fn) => fn());
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('CanvasActionBar', () => {
	it('renders one control per action and runs the action on click', () => {
		const cut = vi.fn();
		const place_ = vi.fn();
		const { target } = place(CanvasActionBar, { actions: [act('cut', cut), act('go', place_, { primary: true })] });
		expect(target.querySelector('[data-canvas-actions="floating"]')).not.toBeNull();
		button(target, 'cut').click();
		button(target, 'go').click();
		expect(cut).toHaveBeenCalledTimes(1);
		expect(place_).toHaveBeenCalledTimes(1);
	});

	it('shows nothing for a tool with no actions', () => {
		const { target } = place(CanvasActionBar, { actions: [] });
		expect(target.querySelector('[data-canvas-actions]')).toBeNull();
	});

	it('marks toggles pressed and switches on click', () => {
		const eraser = vi.fn();
		const { target } = place(CanvasActionBar, {
			actions: [act('brush', undefined, { kind: 'toggle', group: 'tool', pressed: true }), act('eraser', eraser, { kind: 'toggle', group: 'tool', pressed: false })]
		});
		expect(button(target, 'brush').getAttribute('aria-pressed')).toBe('true');
		expect(button(target, 'eraser').getAttribute('aria-pressed')).toBe('false');
		expect(target.querySelector('[data-canvas-cluster="tool"]')).not.toBeNull();
		button(target, 'eraser').click();
		expect(eraser).toHaveBeenCalledTimes(1);
	});

	it('disables an action that cannot run', () => {
		const run = vi.fn();
		const { target } = place(CanvasActionBar, { actions: [act('clear', run, { disabled: true, reason: 'Mark an area first' })] });
		expect(button(target, 'clear').disabled).toBe(true);
		button(target, 'clear').click();
		expect(run).not.toHaveBeenCalled();
	});

	it('writes a range value as the slider moves and shows it in mono', () => {
		const set = vi.fn();
		const { target } = place(CanvasActionBar, {
			actions: [{ id: 'size', label: 'Size', kind: 'value', value: { type: 'range', value: 64, min: 4, max: 300, step: 1, unit: 'px', set } }]
		});
		const input = target.querySelector<HTMLInputElement>('input[type="range"]')!;
		expect(input.value).toBe('64');
		expect(target.querySelector('[data-canvas-kind="value"]')!.textContent).toContain('64 px');
		input.value = '90';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		expect(set).toHaveBeenCalledWith(90);
	});

	it('puts a rule between groups but none before the primary action', () => {
		const { target } = place(CanvasActionBar, { actions: [act('cut', undefined, { group: 'clip' }), act('invert', undefined, { group: 'shape' }), act('go', undefined, { primary: true })] });
		expect(target.querySelectorAll('[data-canvas-actions] > span[aria-hidden="true"]')).toHaveLength(1);
	});

	it('is gone while hidden, and only invisible as a phone strip so the canvas does not jump', () => {
		const floating = place(CanvasActionBar, { actions: [act('cut')], hidden: true });
		expect(floating.target.querySelector('[data-canvas-actions]')).toBeNull();
		const strip = place(CanvasActionBar, { actions: [act('cut')], hidden: true, variant: 'strip' });
		const row = strip.target.querySelector<HTMLElement>('[data-canvas-actions="strip"]')!;
		expect(row.hasAttribute('data-canvas-actions-hidden')).toBe(true);
		expect(row.className).toContain('invisible');
	});

	it('renders a custom component the action brings', () => {
		const Custom = (anchor: Comment) => {
			const span = document.createElement('span');
			span.setAttribute('data-custom-part', '');
			anchor.before(span);
		};
		const { target } = place(CanvasActionBar, { actions: [{ id: 'crop-bar', label: 'Crop', kind: 'custom', component: { component: Custom as never }, props: {} }] });
		expect(target.querySelector('[data-custom-part]')).not.toBeNull();
	});
});

describe('EditorFrame canvas bar', () => {
	function frame(actions: CanvasAction[], phone = false) {
		const toolbar = createRawSnippet(() => ({ render: () => '<div data-toolbar></div>' }));
		const canvas = createRawSnippet(() => ({ render: () => '<div data-surface style="width:100px;height:100px"></div>' }));
		return place(EditorFrame, { phone, onundo: () => undefined, onredo: () => undefined, onsave: () => undefined, toolbar, canvas, canvasActions: actions });
	}

	it('shows the bar in the canvas region', () => {
		const { target } = frame([act('cut')]);
		expect(target.querySelector('[data-frame-canvas] [data-canvas-actions="floating"]')).not.toBeNull();
	});

	it('hides the bar while a stroke is down and brings it back on release', async () => {
		const { target } = frame([act('cut')]);
		const surface = target.querySelector('[data-surface]')!;
		surface.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
		await tick();
		expect(target.querySelector('[data-canvas-actions]')).toBeNull();
		window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
		await tick();
		expect(target.querySelector('[data-canvas-actions="floating"]')).not.toBeNull();
	});

	it('comes back after a cancelled gesture too', async () => {
		const { target } = frame([act('cut')]);
		target.querySelector('[data-surface]')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
		await tick();
		window.dispatchEvent(new PointerEvent('pointercancel', { bubbles: true }));
		await tick();
		expect(target.querySelector('[data-canvas-actions]')).not.toBeNull();
	});

	it('keeps the bar when the press lands on the bar itself', async () => {
		const run = vi.fn();
		const { target } = frame([act('cut', run)]);
		button(target, 'cut').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
		await tick();
		expect(target.querySelector('[data-canvas-actions]')).not.toBeNull();
	});

	it('puts the phone strip above the tool sheet and keeps its space while drawing', async () => {
		const { target } = frame([act('cut')], true);
		const strip = target.querySelector('[data-canvas-actions="strip"]')!;
		expect(strip).not.toBeNull();
		expect(target.querySelector('[data-canvas-actions="floating"]')).toBeNull();
		target.querySelector('[data-surface]')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
		await tick();
		expect(target.querySelector('[data-canvas-actions="strip"]')!.hasAttribute('data-canvas-actions-hidden')).toBe(true);
	});
});

describe('MaskLayer handoff from a selection', () => {
	function alphaOf(canvas: HTMLCanvasElement): Uint8ClampedArray {
		const data = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data;
		const out = new Uint8ClampedArray(canvas.width * canvas.height);
		for (let i = 0; i < out.length; i++) out[i] = data[i * 4 + 3];
		return out;
	}

	async function layer(props: Record<string, unknown>) {
		installPixelCanvas();
		const mounted = place(MaskLayer, { width: 40, height: 30, ...props });
		await tick();
		await new Promise((resolve) => setTimeout(resolve, 0));
		const canvas = mounted.target.querySelector<HTMLCanvasElement>('[data-mask-layer]')!;
		return { ...mounted, canvas };
	}

	it('fills the mask with the selection shape and reports it consumed', async () => {
		const path = [{ x: 5, y: 4 }, { x: 25, y: 6 }, { x: 15, y: 22 }];
		const onseeded = vi.fn();
		const onstroke = vi.fn();
		const { canvas } = await layer({ seed: { path, width: 40, height: 30 }, onseeded, onstroke });
		const expected = rasterizePolygon(path, 40, 30);
		const painted = alphaOf(canvas);
		for (let i = 0; i < expected.length; i++) expect(painted[i] > 0).toBe(expected[i] > 0);
		expect(painted.some((value) => value > 0)).toBe(true);
		expect(onseeded).toHaveBeenCalledTimes(1);
		expect(onstroke).toHaveBeenCalledWith(true);
		expect(get(maskArea)).not.toBeNull();
	});

	it('scales the shape when the picture is a different size from the paint document', async () => {
		const path = [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }];
		const { canvas } = await layer({ seed: { path, width: 20, height: 15 } });
		const painted = alphaOf(canvas);
		expect(painted[15 * 40 + 15]).toBeGreaterThan(0);
		expect(painted[5 * 40 + 25]).toBe(0);
		expect(painted[25 * 40 + 5]).toBe(0);
	});

	it('leaves the mask empty when there is no seed', async () => {
		const { canvas } = await layer({});
		expect(alphaOf(canvas).some((value) => value > 0)).toBe(false);
	});

	it('inverts the marked area and inverts back', async () => {
		const path = [{ x: 5, y: 4 }, { x: 25, y: 6 }, { x: 15, y: 22 }];
		const { canvas, instance } = await layer({ seed: { path, width: 40, height: 30 } });
		const before = alphaOf(canvas);
		(instance.invert as () => void)();
		const inverted = alphaOf(canvas);
		for (let i = 0; i < before.length; i++) expect(inverted[i] > 0).toBe(before[i] === 0);
		(instance.invert as () => void)();
		const again = alphaOf(canvas);
		for (let i = 0; i < before.length; i++) expect(again[i] > 0).toBe(before[i] > 0);
	});
});
