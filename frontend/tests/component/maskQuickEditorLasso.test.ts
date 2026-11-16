import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createClassComponent } from 'svelte/legacy';
import { tick } from 'svelte';
import { installPixelCanvas, placeCanvas, pointer } from './helpers/pixelCanvas';

class StubResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}
vi.stubGlobal('ResizeObserver', StubResizeObserver);

const uploadMedia = vi.fn();
const fetchSpy = vi.fn(async () => new Response('{}'));
vi.stubGlobal('fetch', fetchSpy);

const sent = vi.hoisted(() => ({ canvas: null as HTMLCanvasElement | null }));

vi.mock('$lib/media/loadImage', () => ({
	loadImage: async () => ({ naturalWidth: 400, naturalHeight: 300 })
}));

vi.mock('$lib/services/api/index', () => ({
	api: { uploadMedia: (...args: unknown[]) => uploadMedia(...args) }
}));

const { default: MediaEditors } = await import('$lib/media/editors/MediaEditors.svelte');
const { resetMaskTool } = await import('$lib/tool-tabs/image/maskOverlays');

const source = {
	url: '/api/media/uploads/portrait.png',
	kind: 'image' as const,
	fileName: 'portrait.png',
	itemId: null,
	storedPath: 'uploads/portrait.png',
	width: 400,
	height: 300
};

let pixels: ReturnType<typeof installPixelCanvas>;

async function settle() {
	for (let i = 0; i < 6; i += 1) {
		await tick();
		await new Promise((resolve) => setTimeout(resolve, 0));
	}
}

function mount() {
	const onClose = vi.fn();
	const onResult = vi.fn();
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({
		component: MediaEditors as any,
		target,
		props: { request: { kind: 'mask', source, itemIndex: null }, scope: 'field', onClose, onResult }
	});
	return { onClose, onResult };
}

const buttons = () => Array.from(document.body.querySelectorAll('button'));
const buttonByText = (text: string) => buttons().find((button) => (button.textContent || '').trim() === text);
const layerCanvas = () => document.body.querySelector<HTMLCanvasElement>('[data-mask-layer]')!;

async function drag(path: Array<[number, number]>) {
	const canvas = layerCanvas();
	placeCanvas(canvas, { left: 0, top: 0, width: 400, height: 300 });
	const [first, ...rest] = path;
	canvas.dispatchEvent(pointer('pointerdown', first[0], first[1]));
	for (const [x, y] of rest) canvas.dispatchEvent(pointer('pointermove', x, y));
	const last = rest[rest.length - 1] ?? first;
	canvas.dispatchEvent(pointer('pointerup', last[0], last[1]));
	await settle();
}

function sentMask() {
	const data = pixels.pixelsOf(sent.canvas!);
	const values = new Set<number>();
	for (let i = 0; i < data.length; i += 4) {
		values.add(data[i]);
		expect(data[i + 3]).toBe(255);
	}
	return { data, values: [...values].sort((a, b) => a - b), width: sent.canvas!.width, height: sent.canvas!.height };
}

beforeEach(() => {
	resetMaskTool();
	pixels = installPixelCanvas();
	vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockImplementation(function (this: HTMLCanvasElement) {
		sent.canvas = this;
		return 'data:image/png;base64,iVBORw0KGgo=';
	});
	uploadMedia.mockReset();
	uploadMedia.mockResolvedValue({ success: true, data: { path: 'uploads/mask-9.png' } });
});

afterEach(() => {
	document.body.innerHTML = '';
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
	vi.stubGlobal('ResizeObserver', StubResizeObserver);
	vi.stubGlobal('fetch', fetchSpy);
});

describe('quick mask modal with the lasso', () => {
	it('offers Brush and Lasso with Add and Subtract through the shared mask options', async () => {
		mount();
		await settle();
		expect(buttonByText('Brush')).toBeDefined();
		expect(buttonByText('Lasso')).toBeDefined();
		buttonByText('Lasso')!.click();
		await settle();
		expect(buttonByText('Add')).toBeDefined();
		expect(buttonByText('Subtract')).toBeDefined();
		expect(buttonByText('Clear mask')).toBeDefined();
		expect(buttonByText('Apply')!.disabled).toBe(true);
	});

	it('applies a lasso area as the same mask a brush stroke makes: picture size, opaque, white on black', async () => {
		const brush = mount();
		await settle();
		await drag([[100, 100], [140, 100]]);
		buttonByText('Apply')!.click();
		await settle();
		const fromBrush = sentMask();
		expect(brush.onResult).toHaveBeenCalledWith({ type: 'mask', maskPath: 'uploads/mask-9.png' }, expect.objectContaining({ kind: 'mask' }));

		document.body.innerHTML = '';
		resetMaskTool();
		uploadMedia.mockClear();
		const area = mount();
		await settle();
		buttonByText('Lasso')!.click();
		await settle();
		await drag([[10, 10], [60, 10], [60, 40], [10, 40]]);
		expect(buttonByText('Apply')!.disabled).toBe(false);
		buttonByText('Apply')!.click();
		await settle();
		const fromLasso = sentMask();

		expect(area.onResult).toHaveBeenCalledWith({ type: 'mask', maskPath: 'uploads/mask-9.png' }, expect.objectContaining({ kind: 'mask' }));
		const [file, purpose] = uploadMedia.mock.calls[0];
		expect(purpose).toBe('derived_artifact');
		expect((file as File).type).toBe('image/png');
		expect([fromLasso.width, fromLasso.height]).toEqual([fromBrush.width, fromBrush.height]);
		expect([fromLasso.width, fromLasso.height]).toEqual([400, 300]);
		expect(fromLasso.values).toEqual([0, 255]);
		expect(fromBrush.values).toEqual([0, 255]);
		expect(fromLasso.data[(20 * 400 + 30) * 4]).toBe(255);
		expect(fromLasso.data[0]).toBe(0);
		expect(fromLasso.data[(20 * 400 + 70) * 4]).toBe(0);
	});

	it('blocks Apply again once Clear mask empties a lasso area', async () => {
		mount();
		await settle();
		buttonByText('Lasso')!.click();
		await settle();
		await drag([[10, 10], [60, 10], [60, 40], [10, 40]]);
		expect(buttonByText('Apply')!.disabled).toBe(false);
		buttonByText('Clear mask')!.click();
		await settle();
		expect(buttonByText('Apply')!.disabled).toBe(true);
		expect(uploadMedia).not.toHaveBeenCalled();
	});

	it('cancels a shape in progress on Escape without closing the dialog', async () => {
		const { onClose } = mount();
		await settle();
		buttonByText('Lasso')!.click();
		await settle();
		const canvas = layerCanvas();
		placeCanvas(canvas, { left: 0, top: 0, width: 400, height: 300 });
		canvas.dispatchEvent(pointer('pointerdown', 10, 10));
		canvas.dispatchEvent(pointer('pointermove', 60, 10));
		canvas.dispatchEvent(pointer('pointermove', 60, 40));
		await settle();
		document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
		await settle();
		expect(onClose).not.toHaveBeenCalled();
		canvas.dispatchEvent(pointer('pointerup', 60, 40));
		await settle();
		expect(buttonByText('Apply')!.disabled).toBe(true);
	});
});
