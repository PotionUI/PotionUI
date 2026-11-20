import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createClassComponent } from 'svelte/legacy';
import { tick } from 'svelte';

const log = vi.hoisted(() => ({
	painted: false,
	strokes: 0,
	erases: 0,
	puts: [] as Uint8ClampedArray[],
	created: [] as Array<[number, number]>
}));

class StubResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}
vi.stubGlobal('ResizeObserver', StubResizeObserver);

const uploadMedia = vi.fn();
const fetchSpy = vi.fn(async () => new Response('{}'));
vi.stubGlobal('fetch', fetchSpy);

vi.mock('$lib/media/loadImage', () => ({
	loadImage: async () => ({ naturalWidth: 8, naturalHeight: 6 })
}));

vi.mock('$lib/services/api/index', () => ({
	api: { uploadMedia: (...args: unknown[]) => uploadMedia(...args) }
}));

const { default: MediaEditors } = await import('$lib/media/editors/MediaEditors.svelte');

const source = {
	url: '/api/media/uploads/portrait.png',
	kind: 'image' as const,
	fileName: 'portrait.png',
	itemId: null,
	storedPath: 'uploads/portrait.png',
	width: 8,
	height: 6
};

function stubCanvas() {
	let mode = 'source-over';
	const context = new Proxy(
		{
			getImageData: (_x: number, _y: number, w: number, h: number) => {
				const data = new Uint8ClampedArray(w * h * 4);
				data[0] = 255;
				data[1] = 255;
				data[2] = 255;
				data[3] = log.painted ? 255 : 0;
				return { width: w, height: h, data };
			},
			createImageData: (w: number, h: number) => {
				log.created.push([w, h]);
				return { width: w, height: h, data: new Uint8ClampedArray(w * h * 4) };
			},
			putImageData: (image: { data: Uint8ClampedArray }) => {
				log.puts.push(new Uint8ClampedArray(image.data));
				if (image.data.some((value, index) => index % 4 === 3 && value > 0)) log.painted = true;
			},
			clearRect: () => {
				log.painted = false;
			},
			stroke: () => {
				if (mode === 'destination-out') log.erases += 1;
				else {
					log.strokes += 1;
					log.painted = true;
				}
			}
		} as Record<string | symbol, unknown>,
		{
			get: (target, key) => (key in target ? target[key] : key === 'globalCompositeOperation' ? mode : () => undefined),
			set: (_target, key, value) => {
				if (key === 'globalCompositeOperation') mode = String(value);
				return true;
			}
		}
	);
	vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => context as unknown as CanvasRenderingContext2D);
	vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockImplementation(() => 'data:image/png;base64,iVBORw0KGgo=');
	vi.stubGlobal(
		'ImageData',
		class {
			constructor(
				readonly data: Uint8ClampedArray,
				readonly width: number,
				readonly height: number
			) {}
		}
	);
}

async function settle() {
	for (let i = 0; i < 6; i += 1) {
		await tick();
		await new Promise((resolve) => setTimeout(resolve, 0));
	}
}

function mount(props: Record<string, unknown> = {}) {
	const onClose = vi.fn();
	const onResult = vi.fn();
	const target = document.createElement('div');
	document.body.appendChild(target);
	const view = createClassComponent({
		component: MediaEditors as any,
		target,
		props: {
			request: { kind: 'mask', source, itemIndex: null },
			scope: 'field',
			onClose,
			onResult,
			...props
		}
	});
	return { onClose, onResult, view };
}

const buttons = () => Array.from(document.body.querySelectorAll('button'));
const buttonByText = (text: string) => buttons().find((button) => (button.textContent || '').trim() === text);
const layerCanvas = () => document.body.querySelector<HTMLCanvasElement>('[data-mask-layer]')!;

async function drag() {
	const canvas = layerCanvas();
	canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 8, height: 6, right: 8, bottom: 6, x: 0, y: 0, toJSON: () => ({}) });
	canvas.dispatchEvent(new PointerEvent('pointerdown', { button: 0, clientX: 1, clientY: 1, bubbles: true }));
	canvas.dispatchEvent(new PointerEvent('pointermove', { clientX: 5, clientY: 4, bubbles: true }));
	canvas.dispatchEvent(new PointerEvent('pointerup', { clientX: 5, clientY: 4, bubbles: true }));
	await settle();
}

beforeEach(() => {
	log.painted = false;
	log.strokes = 0;
	log.erases = 0;
	log.puts = [];
	log.created = [];
	uploadMedia.mockReset();
	uploadMedia.mockResolvedValue({ success: true, data: { path: 'uploads/mask-9.png' } });
	fetchSpy.mockClear();
	stubCanvas();
});

afterEach(() => {
	document.body.innerHTML = '';
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
	vi.stubGlobal('ResizeObserver', StubResizeObserver);
	vi.stubGlobal('fetch', fetchSpy);
});

describe('quick mask modal', () => {
	it('opens as a dialog with the tab editor mask controls and blocks Apply until something is painted', async () => {
		mount();
		await settle();

		expect(document.body.querySelector('[role="dialog"]')).not.toBeNull();
		expect(document.body.querySelector('[aria-label="Mask brush"]')).not.toBeNull();
		expect(buttonByText('Add')).toBeDefined();
		expect(buttonByText('Subtract')).toBeDefined();
		expect(buttonByText('Clear mask')).toBeDefined();
		expect(document.body.textContent).toContain('Paint over the area to repaint.');
		expect(buttonByText('Apply')!.disabled).toBe(true);
	});

	it('paints, applies, uploads the mask as a derived artifact and returns its path', async () => {
		const { onClose, onResult } = mount();
		await settle();
		await drag();

		expect(log.strokes).toBeGreaterThan(0);
		expect(buttonByText('Apply')!.disabled).toBe(false);

		buttonByText('Apply')!.click();
		await settle();

		expect(uploadMedia).toHaveBeenCalledTimes(1);
		const [file, purpose] = uploadMedia.mock.calls[0];
		expect(purpose).toBe('derived_artifact');
		expect((file as File).type).toBe('image/png');
		expect((file as File).name).toMatch(/^mask-\d+\.png$/);
		expect(onResult).toHaveBeenCalledWith({ type: 'mask', maskPath: 'uploads/mask-9.png' }, expect.objectContaining({ kind: 'mask' }));
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('writes the mask at the picture size, white where painted and opaque black elsewhere', async () => {
		mount();
		await settle();
		await drag();
		log.puts = [];
		log.created = [];

		buttonByText('Apply')!.click();
		await settle();

		expect(log.created.at(-1)).toEqual([8, 6]);
		const written = log.puts.at(-1)!;
		expect(Array.from(written.slice(0, 4))).toEqual([255, 255, 255, 255]);
		expect(Array.from(written.slice(4, 8))).toEqual([0, 0, 0, 255]);
		expect(written.length).toBe(8 * 6 * 4);
	});

	it('paints with the eraser as a destination-out stroke', async () => {
		mount();
		await settle();
		buttonByText('Subtract')!.click();
		await settle();
		await drag();

		expect(log.erases).toBeGreaterThan(0);
		expect(log.strokes).toBe(0);
	});

	it('loads an existing mask for further painting and lets Apply through untouched', async () => {
		const { onResult } = mount({ existingMaskUrl: '/api/media/uploads/mask-1.png' });
		await settle();

		expect(log.puts.length).toBe(1);
		expect(buttonByText('Apply')!.disabled).toBe(false);

		buttonByText('Apply')!.click();
		await settle();
		expect(onResult).toHaveBeenCalledTimes(1);
	});

	it('clears the mask and blocks Apply again', async () => {
		mount();
		await settle();
		await drag();
		expect(buttonByText('Apply')!.disabled).toBe(false);

		buttonByText('Clear mask')!.click();
		await settle();

		expect(buttonByText('Apply')!.disabled).toBe(true);
		expect(uploadMedia).not.toHaveBeenCalled();
	});

	it('discards on Cancel and on Escape without uploading anything', async () => {
		const first = mount();
		await settle();
		await drag();
		buttonByText('Cancel')!.click();
		await settle();
		expect(first.onClose).toHaveBeenCalledTimes(1);
		expect(first.onResult).not.toHaveBeenCalled();
		expect(uploadMedia).not.toHaveBeenCalled();

		document.body.innerHTML = '';
		const second = mount();
		await settle();
		await drag();
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
		await settle();
		expect(second.onClose).toHaveBeenCalled();
		expect(second.onResult).not.toHaveBeenCalled();
		expect(uploadMedia).not.toHaveBeenCalled();
	});

	it('creates no edit and makes no edits request while it is open, applied or cancelled', async () => {
		mount();
		await settle();
		await drag();
		buttonByText('Apply')!.click();
		await settle();

		const urls = fetchSpy.mock.calls.map((call) => String((call as unknown[])[0]));
		expect(urls.filter((url) => url.includes('/api/edits'))).toEqual([]);
		expect(uploadMedia.mock.calls.every(([, purpose]) => purpose === 'derived_artifact')).toBe(true);
	});

	it('does not mount the image editor tab', async () => {
		mount();
		await settle();
		expect(document.body.querySelector('[data-image-editor-tab]')).toBeNull();
		expect(document.body.querySelectorAll('canvas').length).toBe(1);
	});
});
