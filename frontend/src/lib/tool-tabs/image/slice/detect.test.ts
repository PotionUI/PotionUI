import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { analysePixels, detectPanels, orderRows } from './detect';

interface GoldenCase {
	name: string;
	width: number;
	height: number;
	background: number[];
	noise: { amplitude: number; seed: number } | null;
	transparent: boolean;
	boxes: { x0: number; y0: number; x1: number; y1: number; color: number[] }[];
	tolerance: number;
	epsilon: number;
	expected: { x: number; y: number; w: number; h: number }[];
}

const goldens = JSON.parse(readFileSync(new URL('../../../../../../tests/fixtures/slice_panel_goldens.json', import.meta.url), 'utf8')) as { cases: GoldenCase[] };

function noise(index: number, seed: number, amplitude: number): number {
	let h = (Number((BigInt(index) * 2654435761n) % 4294967296n) + seed * 97) % 4294967296;
	h = Number((BigInt((h ^ (h >>> 15)) >>> 0) * 2246822519n) % 4294967296n);
	h = (h ^ (h >>> 13)) >>> 0;
	return (h % (2 * amplitude + 1)) - amplitude;
}

function raster(c: GoldenCase): Uint8Array {
	const data = new Uint8Array(c.width * c.height * 4);
	const bg = c.transparent ? [0, 0, 0, 0] : [...c.background, 255];
	for (let p = 0; p < c.width * c.height; p++) data.set(bg, p * 4);
	for (const box of c.boxes) {
		for (let y = box.y0; y < box.y1; y++) for (let x = box.x0; x < box.x1; x++) data.set([...box.color.slice(0, 3), 255], (y * c.width + x) * 4);
	}
	if (c.noise && !c.transparent) {
		for (let p = 0; p < c.width * c.height; p++) {
			for (let ch = 0; ch < 3; ch++) {
				const i = p * 4 + ch;
				data[i] = Math.max(0, Math.min(255, data[i] + noise(p * 3 + ch, c.noise.seed, c.noise.amplitude)));
			}
		}
	}
	return data;
}

describe('panel detection against the shared goldens', () => {
	it('has cases to check', () => {
		expect(goldens.cases.length).toBeGreaterThan(0);
	});

	for (const c of goldens.cases) {
		it(c.name, () => {
			const panels = detectPanels(analysePixels(raster(c), c.width, c.height, c.tolerance));
			expect(panels).toHaveLength(c.expected.length);
			panels.forEach((panel, i) => {
				const want = c.expected[i];
				expect(Math.abs(panel.x0 - want.x)).toBeLessThanOrEqual(c.epsilon);
				expect(Math.abs(panel.y0 - want.y)).toBeLessThanOrEqual(c.epsilon);
				expect(Math.abs(panel.x1 - panel.x0 - want.w)).toBeLessThanOrEqual(c.epsilon);
				expect(Math.abs(panel.y1 - panel.y0 - want.h)).toBeLessThanOrEqual(c.epsilon);
			});
		});
	}
});

describe('detection rules', () => {
	function synthetic(w: number, h: number, boxes: [number, number, number, number][]) {
		const data = new Uint8Array(w * h * 4);
		for (let p = 0; p < w * h; p++) data.set([240, 240, 240, 255], p * 4);
		for (const [x0, y0, x1, y1] of boxes) {
			for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) data.set([20, 60, 120, 255], (y * w + x) * 4);
		}
		return analysePixels(data, w, h);
	}

	it('attaches a small speck near a panel instead of dropping it', () => {
		const panels = detectPanels(
			synthetic(200, 200, [
				[20, 20, 100, 100],
				[104, 50, 108, 54]
			])
		);
		expect(panels).toHaveLength(1);
		expect(panels[0].x1).toBeCloseTo(108 / 200, 3);
	});

	it('orders cells row-major with a 4% row tolerance', () => {
		const rects = [
			{ id: 'b', x0: 0.5, y0: 0.02, x1: 0.9, y1: 0.4 },
			{ id: 'a', x0: 0.1, y0: 0.05, x1: 0.4, y1: 0.4 },
			{ id: 'c', x0: 0.1, y0: 0.5, x1: 0.4, y1: 0.9 }
		];
		expect(orderRows(rects).map((r) => r.id)).toEqual(['a', 'b', 'c']);
	});
});
