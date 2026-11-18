import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PaintSession } from './session';
import { rectPath } from './raster/selection';
import { rasterizePolygon } from './raster/selectionMask';
import type { Layer } from './types';

class FakeImageData {
	constructor(
		public data: Uint8ClampedArray,
		public width: number,
		public height: number
	) {}
}

class FakeContext {
	imageSmoothingQuality = 'low';
	private flipX = false;
	constructor(private canvas: FakeCanvas) {}
	translate() {}
	scale(x: number) {
		this.flipX = x < 0;
	}
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
				const to = ((y + row) * this.canvas.width + x + col) * 4;
				const from = (row * image.width + col) * 4;
				for (let c = 0; c < 4; c++) this.canvas.pixels[to + c] = image.data[from + c];
			}
		}
	}
	drawImage(source: FakeCanvas, x: number, y: number) {
		for (let row = 0; row < source.height; row++) {
			for (let col = 0; col < source.width; col++) {
				const column = this.flipX ? source.width - 1 - col : col;
				const dx = x + column;
				const dy = y + row;
				if (dx < 0 || dy < 0 || dx >= this.canvas.width || dy >= this.canvas.height) continue;
				const from = (row * source.width + col) * 4;
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

function buildSession(size = 12) {
	const session = new PaintSession();
	const internal = session as unknown as { doc: { width: number; height: number; layers: Layer[]; activeIndex: number }; ready: boolean };
	const canvas = document.createElement('canvas');
	canvas.width = size;
	canvas.height = size;
	const background: Layer = { id: 1, name: 'Background', canvas, x: 0, y: 0, visible: true, opacity: 1 };
	internal.doc = { width: size, height: size, layers: [background], activeIndex: 0 };
	internal.ready = true;
	return { session, internal, background };
}

function alphaAt(layer: Layer, x: number, y: number): number {
	return (layer.canvas as unknown as FakeCanvas).pixels[(y * layer.canvas.width + x) * 4 + 3];
}

beforeEach(() => {
	vi.stubGlobal('ImageData', FakeImageData);
	vi.stubGlobal('document', { createElement: () => new FakeCanvas() });
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('invertSelection', () => {
	it('selects exactly the pixels the shape left out', () => {
		const { session } = buildSession();
		session.setSelection(rectPath({ x: 2, y: 3 }, { x: 6, y: 7 }), true);
		const before = session.selection!.mask;

		expect(session.invertSelection()).toBe(true);

		const after = session.selection!.mask;
		expect(after.length).toBe(before.length);
		for (let i = 0; i < before.length; i++) expect(after[i] > 0).toBe(before[i] === 0);
	});

	it('gives the shape back when inverted twice', () => {
		const { session } = buildSession();
		const path = [{ x: 2, y: 2 }, { x: 9, y: 3 }, { x: 5, y: 9 }];
		session.setSelection(path, true);
		const original = rasterizePolygon(path, 12, 12);

		session.invertSelection();
		session.invertSelection();

		const again = session.selection!.mask;
		for (let i = 0; i < original.length; i++) expect(again[i] > 0).toBe(original[i] > 0);
	});

	it('does nothing without a selection', () => {
		const { session } = buildSession();
		expect(session.invertSelection()).toBe(false);
		expect(session.selection).toBeNull();
	});

	it('deselects when the shape covered the whole picture', () => {
		const { session } = buildSession();
		session.selectAll();
		session.invertSelection();
		expect(session.selection).toBeNull();
	});
});

describe('clipping to an inverted selection', () => {
	it('clips with the even-odd rule so the hole in the outline stays out', () => {
		const { session, background } = buildSession();
		session.setSelection(rectPath({ x: 2, y: 3 }, { x: 6, y: 7 }), true);
		session.invertSelection();
		const clip = vi.fn();
		const context = { beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), closePath: vi.fn(), clip };
		session.clipToSelection(context as never, background);
		expect(clip).toHaveBeenCalledWith('evenodd');
	});
});

describe('flipFloating', () => {
	function floatingPiece() {
		const { session, internal } = buildSession(12);
		const piece = document.createElement('canvas') as unknown as FakeCanvas;
		piece.width = 4;
		piece.height = 2;
		for (let y = 0; y < 2; y++) piece.pixels.set([255, 0, 0, 255], (y * 4) * 4);
		session.addPieceLayer(piece as unknown as HTMLCanvasElement, 3, 4, 'Piece');
		session.setSelection(rectPath({ x: 3, y: 4 }, { x: 7, y: 6 }), true);
		session.markFloating(internal.doc.layers[1]);
		return { session, layer: internal.doc.layers[1] };
	}

	it('mirrors the piece in place', () => {
		const { session, layer } = floatingPiece();
		expect(alphaAt(layer, 0, 0)).toBe(255);
		expect(alphaAt(layer, 3, 0)).toBe(0);

		expect(session.flipFloating()).toBe(true);

		expect(alphaAt(session.activeLayer!, 3, 0)).toBe(255);
		expect(alphaAt(session.activeLayer!, 0, 0)).toBe(0);
		expect(session.activeLayer!.x).toBe(3);
		expect(session.floatingLayer).not.toBeNull();
	});

	it('flips back on undo', () => {
		const { session } = floatingPiece();
		session.flipFloating();
		expect(alphaAt(session.activeLayer!, 3, 0)).toBe(255);
		session.undo();
		expect(alphaAt(session.activeLayer!, 0, 0)).toBe(255);
		expect(alphaAt(session.activeLayer!, 3, 0)).toBe(0);
	});

	it('does nothing when no piece is floating', () => {
		const { session } = buildSession();
		session.setSelection(rectPath({ x: 2, y: 2 }, { x: 4, y: 4 }), true);
		expect(session.flipFloating()).toBe(false);
	});
});
