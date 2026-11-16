import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { installPixelCanvas, placeCanvas, pointer } from '../helpers/pixelCanvas';

const captured = vi.hoisted(() => ({ canvas: null as HTMLCanvasElement | null }));

vi.mock('$lib/paint/flatten', () => ({
	canvasToPngFile: async (canvas: HTMLCanvasElement, name: string) => {
		captured.canvas = canvas;
		return new File(['png'], name, { type: 'image/png' });
	},
	flattenDocument: () => null
}));

const { default: Fixture } = await import('./MaskToolFixture.svelte');
const { overlays } = await import('$lib/editors/overlays');
const { pluginOwner } = await import('$lib/editors/registry');
const { resetMaskTool } = await import('$lib/tool-tabs/image/maskOverlays');

type Layer = {
	hasStrokes(): boolean;
	clear(): void;
	renderMask(cutoff?: number): HTMLCanvasElement | null;
	exportMask(name: string, cutoff?: number): Promise<File | null>;
};

let pixels: ReturnType<typeof installPixelCanvas>;

async function settle() {
	for (let i = 0; i < 20; i += 1) await Promise.resolve();
	flushSync();
}

async function frame() {
	await new Promise((resolve) => setTimeout(resolve, 40));
	flushSync();
}

async function mountTool(size = { width: 100, height: 80 }, box = { left: 0, top: 0, width: 100, height: 80 }) {
	const strokes: boolean[] = [];
	let layer: Layer | undefined;
	const view = mountComponent(Fixture as never, {
		...size,
		onstroke: (painted: boolean) => strokes.push(painted),
		onready: (ready: Layer) => (layer = ready)
	});
	await settle();
	const canvas = view.target.querySelector<HTMLCanvasElement>('[data-mask-layer]')!;
	placeCanvas(canvas, box);
	return { view, canvas, strokes, layer: () => layer! };
}

const buttons = () => Array.from(document.body.querySelectorAll('button'));
const button = (text: string) => buttons().find((entry) => (entry.textContent || '').trim() === text);

async function choose(text: string) {
	button(text)!.click();
	await settle();
}

async function lasso(canvas: HTMLCanvasElement, path: Array<[number, number]>) {
	const [first, ...rest] = path;
	canvas.dispatchEvent(pointer('pointerdown', first[0], first[1]));
	for (const [x, y] of rest) canvas.dispatchEvent(pointer('pointermove', x, y));
	const last = rest[rest.length - 1] ?? first;
	canvas.dispatchEvent(pointer('pointerup', last[0], last[1]));
	await settle();
}

const box = (x0: number, y0: number, x1: number, y1: number): Array<[number, number]> => [
	[x0, y0],
	[x1, y0],
	[x1, y1],
	[x0, y1]
];

function alpha(canvas: HTMLCanvasElement, x: number, y: number): number {
	return pixels.pixelsOf(canvas)[(y * canvas.width + x) * 4 + 3];
}

beforeEach(() => {
	resetMaskTool();
	pixels = installPixelCanvas();
});

afterEach(() => {
	document.body.innerHTML = '';
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
});

describe('the mask tool with the lasso', () => {
	it('offers Brush and Lasso, shows Paint and Erase for the brush, then Add and Subtract for the lasso', async () => {
		await mountTool();
		expect(button('Brush')).toBeDefined();
		expect(button('Lasso')).toBeDefined();
		expect(document.body.querySelector('[aria-label="Mask brush"]')).not.toBeNull();
		expect(document.body.textContent).toContain('Brush size');
		expect(document.body.textContent).toContain('Paint over the part you want to change.');

		await choose('Lasso');
		expect(document.body.querySelector('[aria-label="Lasso mode"]')).not.toBeNull();
		expect(button('Add')).toBeDefined();
		expect(button('Subtract')).toBeDefined();
		expect(document.body.textContent).not.toContain('Brush size');
		expect(document.body.textContent).toContain('Draw around the part you want to change. Release to close the shape.');
		expect(document.body.querySelector('[data-mask-area-readout]')!.textContent).toContain('None');
	});

	it('fills a released shape into the mask and reports its size in the readout and on the canvas', async () => {
		const { canvas, strokes, layer } = await mountTool();
		await choose('Lasso');
		await lasso(canvas, box(10, 10, 50, 40));

		expect(strokes).toEqual([true]);
		expect(layer().hasStrokes()).toBe(true);
		expect(alpha(canvas, 20, 20)).toBe(255);
		expect(alpha(canvas, 60, 20)).toBe(0);
		expect(document.body.querySelector('[data-mask-area-readout]')!.textContent).toContain('40×30');
		expect(document.body.querySelector('[data-mask-area]')!.textContent).toContain('40×30');
	});

	it('does nothing for a click without a drag', async () => {
		const { canvas, strokes, layer } = await mountTool();
		await choose('Lasso');
		canvas.dispatchEvent(pointer('pointerdown', 20, 20));
		canvas.dispatchEvent(pointer('pointerup', 20, 20));
		await settle();
		expect(strokes).toEqual([]);
		expect(layer().hasStrokes()).toBe(false);
		expect(document.body.querySelector('[data-mask-area]')).toBeNull();
	});

	it('draws an outline while the shape is open and drops it on release', async () => {
		const { canvas } = await mountTool();
		await choose('Lasso');
		canvas.dispatchEvent(pointer('pointerdown', 10, 10));
		canvas.dispatchEvent(pointer('pointermove', 40, 12));
		canvas.dispatchEvent(pointer('pointermove', 30, 40));
		await frame();
		expect(document.body.querySelector('[data-lasso-outline] polygon')!.getAttribute('points')).toBe('10,10 40,12 30,40');
		canvas.dispatchEvent(pointer('pointerup', 30, 40));
		await frame();
		expect(document.body.querySelector('[data-lasso-outline]')).toBeNull();
	});

	it('cancels the shape in progress on Escape and keeps the key from reaching anything else', async () => {
		const { canvas, strokes, layer } = await mountTool();
		await choose('Lasso');
		const reached = vi.fn();
		window.addEventListener('keydown', reached);
		canvas.dispatchEvent(pointer('pointerdown', 10, 10));
		canvas.dispatchEvent(pointer('pointermove', 50, 10));
		canvas.dispatchEvent(pointer('pointermove', 50, 40));
		await frame();
		document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
		await frame();
		expect(document.body.querySelector('[data-lasso-outline]')).toBeNull();
		canvas.dispatchEvent(pointer('pointerup', 50, 40));
		await settle();
		expect(strokes).toEqual([]);
		expect(layer().hasStrokes()).toBe(false);
		expect(reached).not.toHaveBeenCalled();

		document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
		expect(reached).toHaveBeenCalledTimes(1);
		window.removeEventListener('keydown', reached);
	});

	it('adds a second shape to the first and subtracts a third from both', async () => {
		const { canvas } = await mountTool();
		await choose('Lasso');
		await lasso(canvas, box(10, 10, 40, 40));
		await lasso(canvas, box(30, 30, 70, 60));
		expect(alpha(canvas, 15, 15)).toBe(255);
		expect(alpha(canvas, 60, 50)).toBe(255);
		expect(alpha(canvas, 60, 15)).toBe(0);

		await choose('Subtract');
		await lasso(canvas, box(20, 20, 50, 50));
		expect(alpha(canvas, 35, 35)).toBe(0);
		expect(alpha(canvas, 15, 15)).toBe(255);
		expect(alpha(canvas, 60, 50)).toBe(255);
		expect(document.body.querySelector('[data-mask-area-readout]')!.textContent).toContain('60×50');
	});

	it('composes with brush strokes in either order', async () => {
		const { canvas } = await mountTool();
		canvas.dispatchEvent(pointer('pointerdown', 20, 20));
		canvas.dispatchEvent(pointer('pointermove', 60, 20));
		canvas.dispatchEvent(pointer('pointerup', 60, 20));
		await settle();
		expect(alpha(canvas, 40, 20)).toBe(255);

		await choose('Lasso');
		await choose('Subtract');
		await lasso(canvas, box(30, 10, 50, 30));
		expect(alpha(canvas, 40, 20)).toBe(0);
		expect(alpha(canvas, 24, 20)).toBe(255);

		await choose('Add');
		await lasso(canvas, box(60, 50, 90, 70));
		expect(alpha(canvas, 70, 60)).toBe(255);

		await choose('Brush');
		await choose('Erase');
		canvas.dispatchEvent(pointer('pointerdown', 75, 60));
		canvas.dispatchEvent(pointer('pointermove', 80, 60));
		canvas.dispatchEvent(pointer('pointerup', 80, 60));
		await settle();
		expect(alpha(canvas, 78, 60)).toBe(0);
		expect(alpha(canvas, 62, 52)).toBe(255);
	});

	it('keeps Erase and Subtract as one remove mode when the tool changes', async () => {
		const { canvas } = await mountTool();
		await choose('Erase');
		await choose('Lasso');
		expect(button('Subtract')!.getAttribute('aria-pressed')).toBe('true');
		await lasso(canvas, box(10, 10, 40, 40));
		expect(alpha(canvas, 20, 20)).toBe(0);
	});

	it('maps pointer positions to picture pixels when the picture is zoomed and panned', async () => {
		const { canvas } = await mountTool({ width: 100, height: 80 }, { left: 40, top: 20, width: 200, height: 160 });
		await choose('Lasso');
		await lasso(canvas, [
			[80, 40],
			[160, 40],
			[160, 120],
			[80, 120]
		]);
		expect(alpha(canvas, 25, 15)).toBe(255);
		expect(alpha(canvas, 55, 45)).toBe(255);
		expect(alpha(canvas, 15, 15)).toBe(0);
		expect(alpha(canvas, 65, 45)).toBe(0);
		expect(document.body.querySelector('[data-mask-area-readout]')!.textContent).toContain('40×40');
	});

	it('takes a shape that leaves the picture and clips it at the edge', async () => {
		const { canvas } = await mountTool();
		await choose('Lasso');
		await lasso(canvas, box(-30, -30, 20, 20));
		expect(alpha(canvas, 0, 0)).toBe(255);
		expect(alpha(canvas, 19, 19)).toBe(255);
		expect(alpha(canvas, 20, 20)).toBe(0);
		expect(document.body.querySelector('[data-mask-area-readout]')!.textContent).toContain('20×20');
	});

	it('exports a lasso mask as hard black and white at the stored cutoff', async () => {
		const { canvas, layer } = await mountTool();
		await choose('Lasso');
		await lasso(canvas, box(10, 10, 40, 40));

		const out = layer().renderMask(127)!;
		const data = pixels.pixelsOf(out);
		const values = new Set<number>();
		for (let i = 0; i < data.length; i += 4) {
			values.add(data[i]);
			expect(data[i + 3]).toBe(255);
		}
		expect([...values].sort((a, b) => a - b)).toEqual([0, 255]);
		expect(data[(20 * out.width + 20) * 4]).toBe(255);
		expect(data[0]).toBe(0);

		const file = await layer().exportMask('shape-mask.png', 127);
		expect(file?.name).toBe('shape-mask.png');
		const same = pixels.pixelsOf(captured.canvas!);
		expect(Array.from(same)).toEqual(Array.from(data));
	});

	it('clears the whole mask and the readout together', async () => {
		const { canvas, layer } = await mountTool();
		await choose('Lasso');
		await lasso(canvas, box(10, 10, 40, 40));
		layer().clear();
		await settle();
		expect(layer().hasStrokes()).toBe(false);
		expect(document.body.querySelector('[data-mask-area-readout]')!.textContent).toContain('None');
		expect(document.body.querySelector('[data-mask-area]')).toBeNull();
	});

	it('lists an overlay a plugin registers as a third choice and takes it back out', async () => {
		await mountTool();
		expect(buttons().filter((entry) => ['Brush', 'Lasso'].includes((entry.textContent || '').trim()))).toHaveLength(2);
		const stop = overlays.register(
			{ id: 'acme.magic-select', editor: 'image', component: { component: (() => undefined) as never }, produces: 'selection' },
			pluginOwner('acme')
		);
		await settle();
		expect(button('Magic select')).toBeDefined();
		stop();
		await settle();
		expect(button('Magic select')).toBeUndefined();
	});

	it('removes its pointer listeners when it goes away', async () => {
		const { view, canvas } = await mountTool();
		await choose('Lasso');
		const removed = vi.spyOn(canvas, 'removeEventListener');
		view.destroy();
		await settle();
		expect(removed.mock.calls.map((call) => call[0])).toEqual(expect.arrayContaining(['pointerdown', 'pointermove', 'pointerup', 'pointercancel']));
	});
});
