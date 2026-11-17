import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PaintSession } from './session';
import { rectPath } from './raster/selection';
import type { Layer, ToolPointer } from './types';

class FakeImageData {
	constructor(
		public data: Uint8ClampedArray,
		public width: number,
		public height: number
	) {}
}

class FakeContext {
	imageSmoothingQuality = 'low';
	constructor(private canvas: FakeCanvas) {}
	getImageData(x: number, y: number, w: number, h: number) {
		const out = new Uint8ClampedArray(w * h * 4);
		for (let row = 0; row < h; row++) {
			for (let col = 0; col < w; col++) {
				const sx = x + col;
				const sy = y + row;
				if (sx < 0 || sy < 0 || sx >= this.canvas.width || sy >= this.canvas.height) continue;
				const from = (sy * this.canvas.width + sx) * 4;
				const to = (row * w + col) * 4;
				for (let c = 0; c < 4; c++) out[to + c] = this.canvas.pixels[from + c];
			}
		}
		return new FakeImageData(out, w, h);
	}
	putImageData(image: FakeImageData, x: number, y: number) {
		for (let row = 0; row < image.height; row++) {
			for (let col = 0; col < image.width; col++) {
				const dx = x + col;
				const dy = y + row;
				if (dx < 0 || dy < 0 || dx >= this.canvas.width || dy >= this.canvas.height) continue;
				const from = (row * image.width + col) * 4;
				const to = (dy * this.canvas.width + dx) * 4;
				for (let c = 0; c < 4; c++) this.canvas.pixels[to + c] = image.data[from + c];
			}
		}
	}
	drawImage(source: FakeCanvas, x: number, y: number) {
		for (let row = 0; row < source.height; row++) {
			for (let col = 0; col < source.width; col++) {
				const dx = x + col;
				const dy = y + row;
				if (dx < 0 || dy < 0 || dx >= this.canvas.width || dy >= this.canvas.height) continue;
				const from = (row * source.width + col) * 4;
				if (source.pixels[from + 3] === 0) continue;
				const to = (dy * this.canvas.width + dx) * 4;
				for (let c = 0; c < 4; c++) this.canvas.pixels[to + c] = source.pixels[from + c];
			}
		}
	}
}

class FakeCanvas {
	pixels = new Uint8ClampedArray(0);
	private w = 0;
	private h = 0;
	private context = new FakeContext(this);
	get width() {
		return this.w;
	}
	set width(value: number) {
		this.w = value;
		this.pixels = new Uint8ClampedArray(this.w * this.h * 4);
	}
	get height() {
		return this.h;
	}
	set height(value: number) {
		this.h = value;
		this.pixels = new Uint8ClampedArray(this.w * this.h * 4);
	}
	getContext() {
		return this.context;
	}
}

const POINTER: ToolPointer = {
	pressure: 0.5,
	pointerType: 'mouse',
	altKey: false,
	shiftKey: false
};

function paint(layer: Layer, x: number, y: number, value: number) {
	const base = (y * layer.canvas.width + x) * 4;
	const pixels = (layer.canvas as unknown as FakeCanvas).pixels;
	pixels.set([value, value, value, 255], base);
}

function alphaAt(layer: Layer, docX: number, docY: number): number {
	const x = docX - layer.x;
	const y = docY - layer.y;
	if (x < 0 || y < 0 || x >= layer.canvas.width || y >= layer.canvas.height) return 0;
	return (layer.canvas as unknown as FakeCanvas).pixels[(y * layer.canvas.width + x) * 4 + 3];
}

function buildSession() {
	const session = new PaintSession();
	const internal = session as unknown as {
		doc: {
			width: number;
			height: number;
			layers: Layer[];
			activeIndex: number;
		};
		ready: boolean;
	};
	const canvas = document.createElement('canvas');
	canvas.width = 12;
	canvas.height = 12;
	const background: Layer = {
		id: 1,
		name: 'Background',
		canvas,
		x: 0,
		y: 0,
		visible: true,
		opacity: 1
	};
	internal.doc = {
		width: 12,
		height: 12,
		layers: [background],
		activeIndex: 0
	};
	internal.ready = true;
	for (let y = 2; y < 4; y++) for (let x = 2; x < 4; x++) paint(background, x, y, 200);
	session.setSelection(rectPath({ x: 2, y: 2 }, { x: 4, y: 4 }), true);
	return { session, internal, background };
}

function drag(session: PaintSession, from: { x: number; y: number }, to: { x: number; y: number }) {
	const tool = session.getTool('move');
	tool?.pointerDown?.(session, from, POINTER);
	tool?.pointerMove?.(session, to, POINTER);
	tool?.pointerUp?.(session, to, POINTER);
}

function snapshot(layer: Layer): number[] {
	return [...(layer.canvas as unknown as FakeCanvas).pixels];
}

beforeEach(() => {
	vi.stubGlobal('ImageData', FakeImageData);
	vi.stubGlobal('document', { createElement: () => new FakeCanvas() });
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('moving a lifted selection', () => {
	it('keeps one floating piece across repeated drags', () => {
		const { session, internal } = buildSession();
		session.setTool('move');

		drag(session, { x: 3, y: 3 }, { x: 5, y: 3 });
		drag(session, { x: 5, y: 3 }, { x: 7, y: 3 });
		drag(session, { x: 7, y: 3 }, { x: 7, y: 6 });

		expect(internal.doc.layers).toHaveLength(2);
		const piece = internal.doc.layers[1];
		expect(piece.x).toBe(2 + 2 + 2);
		expect(piece.y).toBe(2 + 3);
		expect(session.activeLayer).toBe(piece);
	});

	it('moves a pasted piece without lifting it onto another layer', () => {
		const { session, internal } = buildSession();
		session.copySelection();
		session.pasteClipboard();
		expect(internal.doc.layers).toHaveLength(2);

		drag(session, { x: 3, y: 3 }, { x: 6, y: 3 });
		drag(session, { x: 6, y: 3 }, { x: 8, y: 3 });

		expect(internal.doc.layers).toHaveLength(2);
		expect(internal.doc.layers[1].x).toBe(2 + 3 + 2);
	});

	it('lifts again after a fresh selection is drawn', () => {
		const { session, internal } = buildSession();
		session.setTool('move');
		drag(session, { x: 3, y: 3 }, { x: 5, y: 3 });
		session.setSelection(rectPath({ x: 4, y: 2 }, { x: 6, y: 4 }), true);

		drag(session, { x: 5, y: 3 }, { x: 7, y: 3 });

		expect(internal.doc.layers).toHaveLength(3);
	});

	it('restores the original pixels when the moves are undone one by one', () => {
		const { session, internal, background } = buildSession();
		session.setTool('move');
		const original = snapshot(background);

		drag(session, { x: 3, y: 3 }, { x: 5, y: 3 });
		drag(session, { x: 5, y: 3 }, { x: 7, y: 3 });
		drag(session, { x: 7, y: 3 }, { x: 7, y: 6 });
		expect(alphaAt(background, 3, 3)).toBe(0);

		session.undo();
		expect(internal.doc.layers).toHaveLength(2);
		expect(internal.doc.layers[1].x).toBe(6);
		expect(internal.doc.layers[1].y).toBe(2);
		session.undo();
		expect(internal.doc.layers).toHaveLength(2);
		expect(internal.doc.layers[1].x).toBe(4);
		session.undo();
		expect(internal.doc.layers).toHaveLength(1);
		expect(snapshot(internal.doc.layers[0])).toEqual(original);
		expect(alphaAt(internal.doc.layers[0], 3, 3)).toBe(255);
	});

	it('redo after undo brings the same single piece back', () => {
		const { session, internal } = buildSession();
		session.setTool('move');
		drag(session, { x: 3, y: 3 }, { x: 5, y: 3 });
		drag(session, { x: 5, y: 3 }, { x: 7, y: 3 });

		session.undo();
		session.undo();
		session.redo();
		session.redo();
		drag(session, { x: 7, y: 3 }, { x: 9, y: 3 });

		expect(internal.doc.layers).toHaveLength(2);
		expect(internal.doc.layers[1].x).toBe(8);
	});
});

describe('placing a floating piece', () => {
	function lifted() {
		const built = buildSession();
		built.session.setTool('move');
		drag(built.session, { x: 3, y: 3 }, { x: 5, y: 3 });
		drag(built.session, { x: 5, y: 3 }, { x: 7, y: 3 });
		drag(built.session, { x: 7, y: 3 }, { x: 7, y: 6 });
		return built;
	}

	function clearHidden(pixels: number[]): number[] {
		const out = [...pixels];
		for (let i = 0; i < out.length; i += 4) if (out[i + 3] === 0) out.fill(0, i, i + 3);
		return out;
	}

	function manualComposite(base: number[], piece: Layer, width: number): number[] {
		const out = [...base];
		const pixels = (piece.canvas as unknown as FakeCanvas).pixels;
		for (let row = 0; row < piece.canvas.height; row++) {
			for (let col = 0; col < piece.canvas.width; col++) {
				const from = (row * piece.canvas.width + col) * 4;
				if (pixels[from + 3] === 0) continue;
				const to = ((piece.y + row) * width + piece.x + col) * 4;
				for (let c = 0; c < 4; c++) out[to + c] = pixels[from + c];
			}
		}
		return out;
	}

	it('exposes the floating piece in the snapshot after three moves', () => {
		const { session, internal } = lifted();
		expect(internal.doc.layers).toHaveLength(2);
		expect(session.snapshot().floatingId).toBe(internal.doc.layers[1].id);
	});

	it('merges the piece into the layer below with the pixels of a manual composite', () => {
		const { session, internal, background } = lifted();
		const piece = internal.doc.layers[1];
		const expected = clearHidden(manualComposite(snapshot(background), piece, 12));

		expect(session.placeFloating()).toBe(true);

		expect(internal.doc.layers).toHaveLength(1);
		expect(clearHidden(snapshot(internal.doc.layers[0]))).toEqual(expected);
		expect(session.snapshot().floatingId).toBeNull();
	});

	it('is one undoable change that brings the floating row back', () => {
		const { session, internal } = lifted();
		const piece = internal.doc.layers[1];
		session.placeFloating();

		session.undo();
		expect(internal.doc.layers).toHaveLength(2);
		expect(internal.doc.layers[1]).toBe(piece);
		expect(session.snapshot().floatingId).toBe(piece.id);

		session.redo();
		expect(internal.doc.layers).toHaveLength(1);
		expect(session.snapshot().floatingId).toBeNull();
	});

	it('keeps the piece as its own layer when asked to', () => {
		const { session, internal } = lifted();
		expect(session.keepFloatingAsLayer()).toBe(true);
		expect(internal.doc.layers).toHaveLength(2);
		expect(session.snapshot().floatingId).toBeNull();
		expect(session.placeFloating()).toBe(false);
	});

	it('places once when another tool is chosen', () => {
		const { session, internal } = lifted();
		session.setTool('rect');
		expect(internal.doc.layers).toHaveLength(1);
		session.setTool('brush');
		expect(internal.doc.layers).toHaveLength(1);
		session.undo();
		expect(internal.doc.layers).toHaveLength(2);
	});

	it('does not place when the tool stays on move', () => {
		const { session, internal } = lifted();
		session.setTool('move');
		expect(internal.doc.layers).toHaveLength(2);
	});

	it('places a pasted piece when deselecting', () => {
		const { session, internal } = buildSession();
		session.copySelection();
		session.pasteClipboard();
		expect(internal.doc.layers).toHaveLength(2);
		session.deselect();
		expect(internal.doc.layers).toHaveLength(1);
	});

	it('renames and reorders layers as undoable changes', () => {
		const { session, internal } = buildSession();
		session.addLayer();
		session.addLayer();
		session.renameLayer(1, 'Sky');
		expect(internal.doc.layers[1].name).toBe('Sky');
		session.moveLayerTo(1, 2);
		expect(internal.doc.layers[2].name).toBe('Sky');
		session.undo();
		session.undo();
		expect(internal.doc.layers[1].name).not.toBe('Sky');
	});
});
