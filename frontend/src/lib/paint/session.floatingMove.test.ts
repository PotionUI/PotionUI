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
