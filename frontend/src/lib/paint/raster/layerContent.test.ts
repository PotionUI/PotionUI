import { describe, expect, it } from 'vitest';
import { isCanvasEmpty, selectionTarget } from './layerContent';

function canvasWith(alphas: number[]): HTMLCanvasElement {
	const data = new Uint8ClampedArray(alphas.length * 4);
	alphas.forEach((a, i) => (data[i * 4 + 3] = a));
	return {
		width: alphas.length,
		height: 1,
		getContext: () => ({ getImageData: () => ({ data }) })
	} as unknown as HTMLCanvasElement;
}

describe('isCanvasEmpty', () => {
	it('is true when every pixel is transparent', () => {
		expect(isCanvasEmpty(canvasWith([0, 0, 0]))).toBe(true);
	});

	it('is false when any pixel has alpha', () => {
		expect(isCanvasEmpty(canvasWith([0, 0, 3]))).toBe(false);
	});
});

describe('selectionTarget', () => {
	it('names the layer and flags an empty one', () => {
		expect(selectionTarget('Sketch', false)).toEqual({ label: 'On layer: Sketch', emptyReason: null });
		expect(selectionTarget('Sketch', true)).toEqual({ label: 'On layer: Sketch', emptyReason: 'This layer is empty' });
	});

	it('says nothing without a layer', () => {
		expect(selectionTarget(null, true)).toEqual({ label: '', emptyReason: null });
	});
});
