import { describe, expect, it } from 'vitest';
import { captureDoc, restoreDoc, structuralBytes } from './docState';
import { identityGeometry, isIdentityGeometry, translateGeometry } from './docGeometry';
import { decideMaskFate } from './maskPolicy';
import type { Layer, PaintDocument } from './types';

function canvas(width: number, height: number): HTMLCanvasElement {
	return { width, height } as HTMLCanvasElement;
}

function layer(id: number, width: number, height: number): Layer {
	return {
		id,
		name: `L${id}`,
		canvas: canvas(width, height),
		x: 0,
		y: 0,
		visible: true,
		opacity: 1
	};
}

const geometry = identityGeometry(100, 100);

function doc(...layers: Layer[]): PaintDocument {
	return { width: 100, height: 100, layers, activeIndex: layers.length - 1 };
}

describe('captureDoc and restoreDoc', () => {
	it('puts layer fields, order and size back exactly', () => {
		const a = layer(1, 100, 100);
		const b = layer(2, 20, 20);
		const state = doc(a, b);
		const before = captureDoc(state, null, geometry);

		b.x = 40;
		b.name = 'moved';
		b.canvas = canvas(30, 30);
		state.layers = [b];
		state.width = 10;
		state.activeIndex = 0;

		restoreDoc(state, before);
		expect(state.layers).toEqual([a, b]);
		expect(b.x).toBe(0);
		expect(b.name).toBe('L2');
		expect(b.canvas.width).toBe(20);
		expect(state.width).toBe(100);
		expect(state.activeIndex).toBe(1);
	});

	it('returns the selection that was captured', () => {
		const selection = {
			path: [],
			mask: new Uint8ClampedArray(1),
			bounds: { x: 0, y: 0, width: 1, height: 1 }
		};
		const snapshot = captureDoc(doc(layer(1, 10, 10)), selection, geometry);
		expect(restoreDoc(doc(layer(1, 10, 10)), snapshot).selection).toBe(selection);
	});

	it('restores the transform source a layer had', () => {
		const a = layer(1, 10, 10);
		const source = canvas(40, 40);
		a.source = source;
		const snapshot = captureDoc(doc(a), null, geometry);
		a.source = undefined;
		restoreDoc(doc(a), snapshot);
		expect(a.source).toBe(source);
	});
});

describe('structuralBytes', () => {
	it('is tiny when every canvas is shared between both sides', () => {
		const a = layer(1, 1000, 1000);
		const before = captureDoc(doc(a), null, geometry);
		const after = captureDoc(doc(a), null, geometry);
		expect(structuralBytes(before, after)).toBe(256);
	});

	it('counts only canvases that exist on one side', () => {
		const a = layer(1, 100, 100);
		const before = captureDoc(doc(a), null, geometry);
		const replacement = canvas(50, 50);
		const oldCanvas = a.canvas;
		a.canvas = replacement;
		const after = captureDoc(doc(a), null, geometry);
		expect(oldCanvas).not.toBe(replacement);
		expect(structuralBytes(before, after)).toBe(100 * 100 * 4 + 50 * 50 * 4 + 256);
	});

	it('counts a newly added layer once', () => {
		const a = layer(1, 100, 100);
		const before = captureDoc(doc(a), null, geometry);
		const b = layer(2, 10, 10);
		const after = captureDoc(doc(a, b), null, geometry);
		expect(structuralBytes(before, after)).toBe(10 * 10 * 4 + 256);
	});
});

describe('geometry across undo', () => {
	it('restores the original geometry when a crop is undone', () => {
		const state = doc(layer(1, 100, 100));
		const before = captureDoc(state, null, geometry);
		const cropped = translateGeometry(geometry, -10, -10, 50, 50);
		state.width = 50;
		state.height = 50;
		const after = captureDoc(state, null, cropped);

		expect(isIdentityGeometry(restoreDoc(state, after).geometry)).toBe(false);
		const restored = restoreDoc(state, before);
		expect(isIdentityGeometry(restored.geometry)).toBe(true);
		expect(state.width).toBe(100);
	});

	it('keeps the inpaint mask when a crop is undone before saving', () => {
		const state = doc(layer(1, 100, 100));
		const before = captureDoc(state, null, geometry);
		const cropped = translateGeometry(geometry, -10, -10, 50, 50);
		const after = captureDoc(state, null, cropped);

		const croppedDecision = decideMaskFate({
			hasMask: true,
			geometryChanged: !isIdentityGeometry(restoreDoc(state, after).geometry),
			sourceReplaced: false
		});
		expect(croppedDecision.fate).toBe('clear');

		const undoneDecision = decideMaskFate({
			hasMask: true,
			geometryChanged: !isIdentityGeometry(restoreDoc(state, before).geometry),
			sourceReplaced: false
		});
		expect(undoneDecision.fate).toBe('keep');
	});
});
