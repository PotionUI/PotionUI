// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const runFilters = vi.hoisted(() => vi.fn());

vi.mock('$lib/paint/adjustments/run', () => ({ runFilters }));
vi.mock('$app/environment', () => ({ browser: true }));

const { PaintSession } = await import('./session');

class FakeImageData {
	constructor(
		public data: Uint8ClampedArray,
		public width: number,
		public height: number
	) {}
}

interface Internals {
	doc: { layers: unknown[]; activeIndex: number };
	runAdjust(layer: unknown, steps: unknown[], preview?: boolean): Promise<{ width: number; height: number } | null>;
	scheduleAdjust(): void;
	discardAdjust(): void;
	adjustFrame: number;
	adjustBase: { scale: number } | null;
}

function build(width: number, height: number, fill = 0) {
	const session = new PaintSession();
	const canvas = document.createElement('canvas');
	canvas.width = width;
	canvas.height = height;
	const layer = { name: 'Layer', canvas, x: 0, y: 0 };
	const internals = session as unknown as Internals;
	internals.doc.layers = [layer];
	vi.spyOn(session, 'layerSelectionMask').mockReturnValue(null);
	return { session, internals, layer, fill };
}

beforeEach(() => {
	vi.stubGlobal('ImageData', FakeImageData);
	vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
		const canvas = this;
		return {
			drawImage: () => undefined,
			putImageData: () => undefined,
			getImageData: (_x: number, _y: number, w: number, h: number) => ({ width: w, height: h, data: new Uint8ClampedArray(w * h * 4).fill((canvas as unknown as { fill?: number }).fill ?? 0) })
		} as never;
	});
	runFilters.mockReset();
	runFilters.mockImplementation(async (image: { width: number; height: number; data: Uint8ClampedArray }) => image);
});

afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

describe('Adjust preview resolution', () => {
	it('previews a large layer at the preview size and renders the full size for Apply', async () => {
		const { internals, layer } = build(3200, 1600);
		const preview = await internals.runAdjust(layer, [], true);
		expect(runFilters.mock.calls[0][0]).toMatchObject({ width: 1600, height: 800 });
		expect(preview).toMatchObject({ width: 1600, height: 800 });
		expect(internals.adjustBase?.scale).toBe(0.5);
		const full = await internals.runAdjust(layer, [], false);
		expect(runFilters.mock.calls[1][0]).toMatchObject({ width: 3200, height: 1600 });
		expect(full).toMatchObject({ width: 3200, height: 1600 });
		expect(internals.adjustBase?.scale).toBe(1);
	});

	it('keeps the cached base while the scale is unchanged and rebuilds it when it changes', async () => {
		const { internals, layer } = build(3200, 1600);
		await internals.runAdjust(layer, [], true);
		const first = internals.adjustBase;
		await internals.runAdjust(layer, [], true);
		expect(internals.adjustBase).toBe(first);
		await internals.runAdjust(layer, [], false);
		expect(internals.adjustBase).not.toBe(first);
	});

	it('previews a small layer at full size', async () => {
		const { internals, layer } = build(800, 600);
		await internals.runAdjust(layer, [], true);
		expect(runFilters.mock.calls[0][0]).toMatchObject({ width: 800, height: 600 });
	});

	it('previews at full size under a selection mask so the mask lines up', async () => {
		const { session, internals, layer } = build(3200, 1600);
		vi.spyOn(session, 'layerSelectionMask').mockReturnValue(new Uint8ClampedArray(3200 * 1600));
		await internals.runAdjust(layer, [], true);
		expect(runFilters.mock.calls[0][0]).toMatchObject({ width: 3200, height: 1600 });
	});
});

describe('Adjust scheduling', () => {
	it('runs one frame for a burst of changes and drops it when the adjustment is discarded', () => {
		const frames: Array<() => void> = [];
		const cancel = vi.fn();
		vi.stubGlobal('requestAnimationFrame', (run: () => void) => frames.push(run));
		vi.stubGlobal('cancelAnimationFrame', cancel);
		const { internals } = build(100, 100);
		internals.scheduleAdjust();
		internals.scheduleAdjust();
		internals.scheduleAdjust();
		expect(frames).toHaveLength(1);
		internals.discardAdjust();
		expect(cancel).toHaveBeenCalledTimes(1);
		expect(internals.adjustFrame).toBe(0);
	});
});

describe('committing a brush stroke from pixel data', () => {
	it('records a patch from before-pixels handed in as ImageData', () => {
		const { session, layer } = build(8, 8);
		(layer.canvas as unknown as { fill: number }).fill = 255;
		const before = new FakeImageData(new Uint8ClampedArray(4 * 4 * 4), 4, 4) as unknown as ImageData;
		session.commitPixels('Brush stroke', layer as never, { x: 2, y: 2, width: 4, height: 4 }, before);
		expect(session.history.length).toBe(1);
		expect(session.history.undoLabel).toBe('Brush stroke');
		expect(session.history.bytes).toBe(2 * 4 * 4 * 4);
	});

	it('records nothing when the pixels did not change', () => {
		const { session, layer } = build(8, 8);
		const before = new FakeImageData(new Uint8ClampedArray(4 * 4 * 4), 4, 4) as unknown as ImageData;
		session.commitPixels('Brush stroke', layer as never, { x: 2, y: 2, width: 4, height: 4 }, before);
		expect(session.history.length).toBe(0);
	});
});
