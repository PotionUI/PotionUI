// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createBrushTool } from './brush';

function setup() {
	const order: string[] = [];
	const layerContext = {
		getImageData: (_x: number, _y: number, w: number, h: number) => {
			order.push('read-before');
			return { width: w, height: h, data: new Uint8ClampedArray(w * h * 4), tag: 'before' };
		},
		save: () => undefined,
		restore: () => undefined,
		drawImage: () => order.push('merge'),
		globalAlpha: 1,
		globalCompositeOperation: 'source-over'
	};
	const bufferContext = new Proxy({}, { get: () => () => undefined, set: () => true });
	const layerCanvas = document.createElement('canvas');
	layerCanvas.width = 200;
	layerCanvas.height = 100;
	const buffers: HTMLCanvasElement[] = [];
	vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
		if (this === layerCanvas) return layerContext as never;
		if (!buffers.includes(this)) buffers.push(this);
		return bufferContext as never;
	});
	const layer = { canvas: layerCanvas, x: 0, y: 0 };
	const commitPixels = vi.fn(() => order.push('commit'));
	const snapshotLayer = vi.fn();
	const host = {
		activeLayer: layer,
		settings: { size: 10, opacity: 100, color: '#fff' },
		snapshotLayer,
		commitPixels,
		clipToSelection: () => undefined,
		setStrokePreview: () => undefined,
		invalidate: () => undefined,
		pickColor: () => undefined
	};
	return { host, layer, order, commitPixels, snapshotLayer, buffers };
}

const pointer = { pressure: 0.5, pointerType: 'mouse', altKey: false, shiftKey: false };

beforeEach(() => vi.stubGlobal('devicePixelRatio', 1));
afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

describe('brush stroke commit', () => {
	it('hands the dirty-rect pixels read before the merge to commitPixels and never snapshots the layer', () => {
		const { host, order, commitPixels, snapshotLayer } = setup();
		const tool = createBrushTool();
		tool.pointerDown!(host as never, { x: 20, y: 20 }, pointer);
		tool.pointerMove!(host as never, { x: 60, y: 30 }, pointer);
		tool.pointerUp!(host as never, { x: 60, y: 30 }, pointer);
		expect(snapshotLayer).not.toHaveBeenCalled();
		expect(order).toEqual(['read-before', 'merge', 'commit']);
		const [label, , rect, before] = commitPixels.mock.calls[0] as unknown as [string, unknown, { width: number; height: number }, { tag: string; width: number; height: number }];
		expect(label).toBe('Brush stroke');
		expect(before.tag).toBe('before');
		expect([before.width, before.height]).toEqual([rect.width, rect.height]);
	});

	it('reuses the stroke buffer for the next stroke', () => {
		const { host, buffers } = setup();
		const tool = createBrushTool();
		for (let i = 0; i < 2; i++) {
			tool.pointerDown!(host as never, { x: 20, y: 20 }, pointer);
			tool.pointerUp!(host as never, { x: 20, y: 20 }, pointer);
		}
		expect(buffers).toHaveLength(1);
	});
});
