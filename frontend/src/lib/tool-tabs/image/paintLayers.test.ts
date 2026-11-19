import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EditStackState, EditStep } from '$lib/tool-tabs/frame/editStack';

const stored = new Map<string, FakeCanvas>();

vi.mock('$lib/paint/loadPicked', () => ({
	canvasFromUrl: async (url: string) => {
		const canvas = stored.get(url);
		if (!canvas) throw new Error(`missing ${url}`);
		return canvas;
	}
}));

const { PaintLayerStore, diffCommand, localLayerCanvas, newLayer, nextPaintName, relinkLayer } = await import('./paintLayers');

class FakeImageData {
	constructor(
		public data: Uint8ClampedArray,
		public width: number,
		public height: number
	) {}
}

class FakeContext {
	imageSmoothingQuality = 'low';
	globalAlpha = 1;
	constructor(private canvas: FakeCanvas) {}
	getImageData(x: number, y: number, w: number, h: number) {
		const out = new Uint8ClampedArray(w * h * 4);
		for (let row = 0; row < h; row++)
			for (let col = 0; col < w; col++) {
				const from = ((y + row) * this.canvas.width + x + col) * 4;
				out.set(this.canvas.pixels.subarray(from, from + 4), (row * w + col) * 4);
			}
		return new FakeImageData(out, w, h);
	}
	putImageData(image: FakeImageData, x: number, y: number) {
		for (let row = 0; row < image.height; row++)
			for (let col = 0; col < image.width; col++) {
				const to = ((y + row) * this.canvas.width + x + col) * 4;
				this.canvas.pixels.set(image.data.subarray((row * image.width + col) * 4, (row * image.width + col) * 4 + 4), to);
			}
	}
	clearRect() {
		this.canvas.pixels.fill(0);
	}
	drawImage(source: FakeCanvas, x = 0, y = 0) {
		for (let row = 0; row < source.height; row++)
			for (let col = 0; col < source.width; col++) {
				const tx = x + col;
				const ty = y + row;
				if (tx < 0 || ty < 0 || tx >= this.canvas.width || ty >= this.canvas.height) continue;
				const from = (row * source.width + col) * 4;
				if (source.pixels[from + 3] === 0) continue;
				this.canvas.pixels.set(source.pixels.subarray(from, from + 4), (ty * this.canvas.width + tx) * 4);
			}
	}
}

class FakeCanvas {
	width = 1;
	height = 1;
	private store: Uint8ClampedArray | null = null;
	get pixels(): Uint8ClampedArray {
		if (!this.store || this.store.length !== this.width * this.height * 4) this.store = new Uint8ClampedArray(this.width * this.height * 4);
		return this.store;
	}
	getContext() {
		return new FakeContext(this);
	}
	toBlob(done: (blob: Blob) => void) {
		done(new Blob([this.pixels.slice()]));
	}
	toDataURL() {
		return 'data:image/png;base64,';
	}
}

function canvas(width: number, height: number, alpha = 0): FakeCanvas {
	const out = new FakeCanvas();
	out.width = width;
	out.height = height;
	if (alpha) for (let i = 3; i < out.pixels.length; i += 4) out.pixels[i] = alpha;
	return out;
}

function step(id: string, url: string, extra: Partial<EditStep> = {}): EditStep {
	return { id, tool: 'paint', icon: 'paint', label: id, summary: 'Paint layer', params: { layer: url ? `art-${url}` : '', url, layers: 1, name: id }, hidden: false, ...extra };
}

function makeStore(upload = vi.fn(async () => ({ id: 'art-next', url: '/next.png' }))) {
	const uploaded = vi.fn();
	const store = new PaintLayerStore({ upload, uploaded, changed: vi.fn(), failed: vi.fn(), delay: 800 });
	return { store, upload, uploaded };
}

beforeEach(() => {
	vi.useFakeTimers();
	vi.stubGlobal('document', { createElement: () => new FakeCanvas() });
	vi.stubGlobal('ImageData', FakeImageData);
	stored.clear();
});

afterEach(() => {
	vi.useRealTimers();
	vi.unstubAllGlobals();
});

describe('relinkLayer', () => {
	it('points every copy of the layer at the uploaded file and keeps the removal undo', () => {
		const one = step('s1', '/a.png');
		const two = step('s2', '/a.png');
		const steps = [one, two];
		const state: EditStackState<unknown> = {
			steps,
			cursor: 2,
			pending: null,
			seq: 2,
			revisions: [{ id: 's1', at: 2, before: { ...one, label: 'old' }, after: one }],
			cleared: { steps: [one], cursor: 1, label: 'Duplicate', after: steps }
		};
		const next = relinkLayer(state, 's1', '/a.png', { id: 'art-b', url: '/b.png' });
		expect(next.steps.map((entry) => (entry.params as { url: string }).url)).toEqual(['/b.png', '/a.png']);
		expect((next.steps[0].params as { layer: string }).layer).toBe('art-b');
		expect((next.revisions![0].before.params as { url: string }).url).toBe('/b.png');
		expect(next.cleared!.after).toBe(next.steps);
		expect((next.cleared!.steps[0].params as { url: string }).url).toBe('/b.png');
	});

	it('leaves a layer that already moved on to another file alone', () => {
		const state: EditStackState<unknown> = { steps: [step('s1', '/merged.png')], cursor: 1, pending: null, seq: 1, cleared: { steps: [step('s1', '/a.png')], cursor: 1, label: 'Merge', after: [] } };
		const next = relinkLayer(state, 's1', '/a.png', { id: 'art-b', url: '/b.png' });
		expect((next.steps[0].params as { url: string }).url).toBe('/merged.png');
		expect((next.cleared!.steps[0].params as { url: string }).url).toBe('/b.png');
	});
});

describe('PaintLayerStore', () => {
	it('uploads a painted layer once after the strokes stop and reports the new file', async () => {
		const { store, upload, uploaded } = makeStore();
		store.adopt('s1', newLayer('Paint 1', canvas(4, 4, 255) as unknown as HTMLCanvasElement), 4, 4);
		store.touch('s1');
		store.touch('s1');
		expect(store.unsaved).toBe(1);
		await vi.advanceTimersByTimeAsync(799);
		expect(upload).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(1);
		expect(upload).toHaveBeenCalledTimes(1);
		expect(uploaded).toHaveBeenCalledWith('s1', '', { id: 'art-next', url: '/next.png' });
		expect(store.unsaved).toBe(0);
	});

	it('uploads again when a stroke lands while a save is running', async () => {
		let finish: (value: { id: string; url: string }) => void = () => undefined;
		const upload = vi.fn(() => new Promise<{ id: string; url: string }>((resolve) => (finish = resolve)));
		const { store, uploaded } = makeStore(upload);
		store.adopt('s1', newLayer('Paint 1', canvas(2, 2) as unknown as HTMLCanvasElement), 2, 2);
		const flushed = store.flush();
		await vi.advanceTimersByTimeAsync(0);
		store.touch('s1');
		finish({ id: 'art-1', url: '/one.png' });
		await vi.advanceTimersByTimeAsync(0);
		expect(uploaded).not.toHaveBeenCalled();
		finish({ id: 'art-2', url: '/two.png' });
		expect(await flushed).toBe(true);
		expect(uploaded).toHaveBeenCalledTimes(1);
		expect(uploaded).toHaveBeenCalledWith('s1', '', { id: 'art-2', url: '/two.png' });
	});

	it('shows the in-memory pixels to the renderer under a versioned local address', async () => {
		const { store } = makeStore();
		const pixels = canvas(2, 2, 200);
		store.adopt('s1', newLayer('Paint 1', pixels as unknown as HTMLCanvasElement), 2, 2);
		const [shown] = store.display([step('s1', '')]);
		const url = (shown.params as { url: string }).url;
		expect(url).toMatch(/^local-layer:/);
		expect(localLayerCanvas(url)).toBe(pixels);
		store.touch('s1');
		expect((store.display([step('s1', '')])[0].params as { url: string }).url).not.toBe(url);
		const other = step('s9', '/x.png');
		expect(store.display([other])[0]).toBe(other);
	});

	it('loads a stored layer stretched to the picture and reloads it when undo points the step at another file', async () => {
		stored.set('/a.png', canvas(2, 2, 10));
		stored.set('/old.png', canvas(2, 2, 90));
		const { store } = makeStore();
		const layer = await store.load(step('s1', '/a.png'), 2, 2);
		expect(layer.canvas.getContext('2d')!.getImageData(0, 0, 1, 1).data[3]).toBe(10);
		store.sync([step('s1', '/old.png')]);
		await vi.advanceTimersByTimeAsync(0);
		expect(layer.canvas.getContext('2d')!.getImageData(0, 0, 1, 1).data[3]).toBe(90);
		expect(store.unsaved).toBe(0);
	});

	it('copies a duplicated layer from memory, unsaved strokes included', async () => {
		const { store, upload } = makeStore();
		store.adopt('s1', newLayer('Paint 1', canvas(2, 2, 70) as unknown as HTMLCanvasElement), 2, 2);
		store.copy('s1', 's2', '');
		expect(store.canvasOf('s2')!.getContext('2d')!.getImageData(1, 1, 1, 1).data[3]).toBe(70);
		expect(await store.flush(['s2'])).toBe(true);
		expect(upload).toHaveBeenCalledTimes(1);
	});
});

describe('diffCommand', () => {
	it('undoes and redoes a change made outside the stroke history, placing a moved piece first', () => {
		const before = canvas(4, 4);
		const piece = canvas(2, 2, 255);
		const layer = newLayer('Picture', piece as unknown as HTMLCanvasElement, 1, 1);
		const command = diffCommand(layer, before as unknown as HTMLCanvasElement, 4, 4)!;
		expect(layer.canvas.width).toBe(4);
		const alpha = () => layer.canvas.getContext('2d')!.getImageData(1, 1, 1, 1).data[3];
		expect(alpha()).toBe(255);
		command.undo();
		expect(alpha()).toBe(0);
		command.redo();
		expect(alpha()).toBe(255);
		expect(command.pixels).toBe(true);
	});

	it('names new layers after the first free Paint number', () => {
		expect(nextPaintName(['Paint 1', 'Paint 3'])).toBe('Paint 2');
	});
});
