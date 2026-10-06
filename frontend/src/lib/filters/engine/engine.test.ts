import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
	COLOUR_OPS,
	CubeError,
	OPS,
	applyFilter,
	applyLut,
	compileLut,
	getOp,
	grainCell,
	grainNoise,
	identityLut,
	lowbias32,
	parseCube,
	resolveValues,
	splitSteps
} from './index';
import type { Cube, FilterStep, Lut, OpSpec } from './index';

interface LutSamples {
	size: number;
	indices: number[];
	values: number[][];
}

interface GoldenImage {
	width: number;
	height: number;
	pixels: number[];
}

function golden<T>(name: string): T {
	const url = new URL(`../../../../../tests/fixtures/filters/golden/${name}`, import.meta.url);
	return JSON.parse(readFileSync(fileURLToPath(url), 'utf-8')) as T;
}

function rgba(image: GoldenImage): { data: Uint8ClampedArray; width: number; height: number } {
	const data = new Uint8ClampedArray(image.width * image.height * 4);
	for (let i = 0; i < image.width * image.height; i++) {
		data[i * 4] = image.pixels[i * 3];
		data[i * 4 + 1] = image.pixels[i * 3 + 1];
		data[i * 4 + 2] = image.pixels[i * 3 + 2];
		data[i * 4 + 3] = 255;
	}
	return { data, width: image.width, height: image.height };
}

function rgbOf(data: Uint8ClampedArray): number[] {
	const out: number[] = [];
	for (let i = 0; i < data.length; i += 4) out.push(data[i], data[i + 1], data[i + 2]);
	return out;
}

function maxDiff(actual: number[], expected: number[]): number {
	expect(actual.length).toBe(expected.length);
	let worst = 0;
	for (let i = 0; i < actual.length; i++) worst = Math.max(worst, Math.abs(actual[i] - expected[i]));
	return worst;
}

function colourSteps(steps: FilterStep[]): FilterStep[] {
	return steps.filter((step) => getOp(step.op)?.kind === 'colour');
}

function checkSamples(lut: Lut, samples: LutSamples): void {
	expect(lut.size).toBe(samples.size);
	samples.indices.forEach((index, n) => {
		for (let k = 0; k < 3; k++) {
			expect(Math.abs(lut.data[index * 3 + k] - samples.values[n][k])).toBeLessThan(1e-4);
		}
	});
}

describe('op catalogue', () => {
	it('matches the Python catalogue exactly', () => {
		const file = golden<{ catalogue: OpSpec[] }>('ops.json');
		expect(JSON.parse(JSON.stringify(OPS))).toEqual(file.catalogue);
	});
});

describe('golden: every op', () => {
	const file = golden<{
		image: GoldenImage;
		tolerance: number;
		cases: {
			name: string;
			steps: FilterStep[];
			intensity: number;
			expected: number[];
			lut_samples?: LutSamples;
		}[];
	}>('ops.json');

	for (const testCase of file.cases) {
		it(testCase.name, () => {
			const image = rgba(file.image);
			applyFilter(image, { steps: testCase.steps }, testCase.intensity);
			expect(maxDiff(rgbOf(image.data), testCase.expected)).toBeLessThanOrEqual(file.tolerance);
			if (testCase.lut_samples) {
				checkSamples(compileLut(testCase.steps, null, testCase.intensity), testCase.lut_samples);
			}
		});
	}

	it('covers every core op', () => {
		const used = new Set(file.cases.flatMap((c) => c.steps.map((s) => s.op)));
		for (const op of OPS) expect(used.has(op.id)).toBe(true);
	});
});

describe('golden: built-in filters at 0, 50 and 100', () => {
	const file = golden<{
		image: GoldenImage;
		tolerance: number;
		cases: {
			name: string;
			filter: string;
			steps: FilterStep[];
			intensity: number;
			expected: number[];
			lut_samples: LutSamples;
		}[];
	}>('filters.json');

	it('has the twelve filters at three intensities', () => {
		expect(new Set(file.cases.map((c) => c.filter)).size).toBe(12);
		expect(file.cases.length).toBe(36);
	});

	for (const testCase of file.cases) {
		it(testCase.name, () => {
			const image = rgba(file.image);
			const input = rgbOf(image.data);
			applyFilter(image, { steps: testCase.steps }, testCase.intensity);
			const out = rgbOf(image.data);
			expect(maxDiff(out, testCase.expected)).toBeLessThanOrEqual(file.tolerance);
			if (testCase.intensity === 0) expect(out).toEqual(input);
			checkSamples(
				compileLut(colourSteps(testCase.steps), null, testCase.intensity),
				testCase.lut_samples
			);
		});
	}
});

describe('golden: spatial ops on larger images', () => {
	const file = golden<{
		cases: {
			name: string;
			width: number;
			height: number;
			fill: number[];
			steps: FilterStep[];
			intensity: number;
			crop: { x: number; y: number; width: number; height: number };
			expected: number[];
		}[];
	}>('spatial.json');

	for (const testCase of file.cases) {
		it(testCase.name, () => {
			const data = new Uint8ClampedArray(testCase.width * testCase.height * 4);
			for (let i = 0; i < testCase.width * testCase.height; i++) {
				data.set([...testCase.fill, 255], i * 4);
			}
			applyFilter(
				{ data, width: testCase.width, height: testCase.height },
				{ steps: testCase.steps },
				testCase.intensity
			);
			const { x, y, width, height } = testCase.crop;
			const crop: number[] = [];
			for (let row = y; row < y + height; row++) {
				for (let col = x; col < x + width; col++) {
					const i = (row * testCase.width + col) * 4;
					crop.push(data[i], data[i + 1], data[i + 2]);
				}
			}
			const grainOnly = testCase.steps.every((s) => s.op === 'grain');
			expect(maxDiff(crop, testCase.expected)).toBeLessThanOrEqual(grainOnly ? 0 : 1);
		});
	}
});

describe('golden: grain hash', () => {
	const file = golden<{
		inputs: number[];
		hashes: number[];
		cells: number[][];
		noise: number[];
	}>('hash.json');

	it('lowbias32 matches exactly', () => {
		file.inputs.forEach((input, i) => expect(lowbias32(input)).toBe(file.hashes[i]));
	});

	it('cell noise matches exactly', () => {
		file.cells.forEach(([cx, cy, seed], i) => expect(grainNoise(cx, cy, seed)).toBe(file.noise[i]));
	});

	it('rounds the grain cell half up', () => {
		expect(grainCell(4, 625, 625)).toBe(3);
		expect(grainCell(1, 24, 16)).toBe(1);
		expect(grainCell(4, 400, 400)).toBe(2);
	});
});

describe('golden: .cube files', () => {
	const file = golden<{
		image: GoldenImage;
		tolerance: number;
		cases: {
			name: string;
			cube: string;
			steps: FilterStep[];
			intensity: number;
			expected: number[];
			lut_samples: LutSamples;
		}[];
		bad: { name: string; cube: string; error: string }[];
	}>('cube.json');

	for (const testCase of file.cases) {
		it(testCase.name, () => {
			const cube = parseCube(testCase.cube);
			const image = rgba(file.image);
			applyFilter(image, { steps: testCase.steps, cube }, testCase.intensity);
			expect(maxDiff(rgbOf(image.data), testCase.expected)).toBeLessThanOrEqual(file.tolerance);
			checkSamples(
				compileLut(colourSteps(testCase.steps), cube, testCase.intensity),
				testCase.lut_samples
			);
		});
	}

	for (const bad of file.bad) {
		it(`rejects ${bad.name}`, () => {
			expect(() => parseCube(bad.cube)).toThrow(CubeError);
			expect(() => parseCube(bad.cube)).toThrow(bad.error);
		});
	}
});

describe('tone op against the legacy Adjust kernel', () => {
	const kernels = Object.values(
		import.meta.glob('../../components/imageEditor/{filters,adjustments}/kernels.ts', {
			eager: true
		})
	)[0] as { applyTone: (data: Uint8ClampedArray, params: Record<string, number>) => void };

	const settings = [
		{ brightness: 30, contrast: 0, saturation: 0, hue: 0 },
		{ brightness: -45, contrast: 20, saturation: -30, hue: 0 },
		{ brightness: 0, contrast: -60, saturation: 40, hue: 75 },
		{ brightness: 10, contrast: 80, saturation: 100, hue: -120 },
		{ brightness: -100, contrast: 100, saturation: -100, hue: 180 },
		{ brightness: 100, contrast: -100, saturation: 100, hue: 33 }
	];

	function everyPixel(): Uint8ClampedArray {
		const levels = [0, 1, 17, 64, 100, 127, 128, 150, 200, 254, 255];
		const out: number[] = [];
		for (const r of levels) for (const g of levels) for (const b of levels) out.push(r, g, b, 255);
		return new Uint8ClampedArray(out);
	}

	for (const values of settings) {
		it(`is pixel-identical to applyTone for ${JSON.stringify(values)}`, () => {
			const legacy = everyPixel();
			kernels.applyTone(legacy, values);
			const spec = getOp('tone')!;
			const map = COLOUR_OPS.tone(resolveValues(spec, { op: 'tone', ...values }));
			const mine = everyPixel();
			const c = new Float64Array(3);
			for (let i = 0; i < mine.length; i += 4) {
				c[0] = mine[i] / 255;
				c[1] = mine[i + 1] / 255;
				c[2] = mine[i + 2] / 255;
				map(c);
				for (let k = 0; k < 3; k++) {
					mine[i + k] = Math.floor(Math.min(1, Math.max(0, c[k])) * 255 + 0.5);
				}
			}
			expect(maxDiff(rgbOf(mine), rgbOf(legacy))).toBeLessThanOrEqual(1);
		});
	}
});

describe('LUT compile against direct per-pixel evaluation', () => {
	const steps: FilterStep[] = [
		{ op: 'white_balance', temperature: 30, tint: -10 },
		{ op: 'curves', master: [[0, 0.02], [0.25, 0.22], [0.75, 0.8], [1, 0.98]] },
		{ op: 'vibrance', amount: 40 },
		{ op: 'fade', black_lift: 5, white_cap: 3 }
	];

	function direct(rgb: number[], list: FilterStep[]): number[] {
		const c = Float64Array.from(rgb);
		for (const step of colourSteps(list)) {
			COLOUR_OPS[step.op](resolveValues(getOp(step.op)!, step))(c);
			for (let k = 0; k < 3; k++) c[k] = Math.min(1, Math.max(0, c[k]));
		}
		return [...c];
	}

	it('stores the direct result at every lattice node', () => {
		const lut = compileLut(steps);
		const m = lut.size - 1;
		for (const index of [0, 1, 33, 1089, 5000, 20000, 35936]) {
			const r = index % lut.size;
			const g = Math.floor(index / lut.size) % lut.size;
			const b = Math.floor(index / (lut.size * lut.size));
			const expected = direct([r / m, g / m, b / m], steps);
			for (let k = 0; k < 3; k++) {
				expect(Math.abs(lut.data[index * 3 + k] - expected[k])).toBeLessThan(1e-6);
			}
		}
	});

	it('stays within three levels of evaluating the ops on every pixel', () => {
		const lut = compileLut(steps);
		const probe: number[] = [];
		let state = 99;
		for (let i = 0; i < 2000; i++) {
			state = (Math.imul(state, 1103515245) + 12345) & 0x7fffffff;
			probe.push((state >> 16) & 255);
		}
		const data = new Uint8ClampedArray((probe.length / 3 | 0) * 4);
		for (let i = 0; i < data.length / 4; i++) {
			data.set([probe[i * 3], probe[i * 3 + 1], probe[i * 3 + 2], 255], i * 4);
		}
		const input = data.slice();
		applyLut(data, lut);
		for (let i = 0; i < data.length; i += 4) {
			const expected = direct([input[i] / 255, input[i + 1] / 255, input[i + 2] / 255], steps);
			for (let k = 0; k < 3; k++) {
				expect(Math.abs(data[i + k] - Math.floor(expected[k] * 255 + 0.5))).toBeLessThanOrEqual(3);
			}
		}
	});

	it('reproduces a larger cube exactly by compiling at the cube size', () => {
		const size = 40;
		const m = size - 1;
		const data = new Float32Array(size ** 3 * 3);
		let o = 0;
		for (let b = 0; b < size; b++) {
			for (let g = 0; g < size; g++) {
				for (let r = 0; r < size; r++) {
					data[o++] = Math.min(1, (r / m) * 0.9 + 0.05);
					data[o++] = Math.min(1, (g / m) * 0.8 + 0.1 * (b / m));
					data[o++] = 1 - b / m;
				}
			}
		}
		const cube: Cube = { size, data, domainMin: [0, 0, 0], domainMax: [1, 1, 1], title: '' };
		const lut = compileLut([], cube);
		expect(lut.size).toBe(40);
		expect(Array.from(lut.data)).toEqual(Array.from(data));
	});
});

describe('intensity and identity', () => {
	it('an identity lattice leaves every level untouched', () => {
		const data = new Uint8ClampedArray(256 * 4);
		for (let v = 0; v < 256; v++) data.set([v, 255 - v, (v * 7) % 256, 200], v * 4);
		const before = data.slice();
		applyLut(data, identityLut());
		expect(Array.from(data)).toEqual(Array.from(before));
	});

	it('leaves pixels and alpha alone at intensity 0', () => {
		const image = { data: new Uint8ClampedArray([10, 20, 30, 77]), width: 1, height: 1 };
		applyFilter(image, { steps: [{ op: 'invert' }, { op: 'grain', amount: 100 }] }, 0);
		expect(Array.from(image.data)).toEqual([10, 20, 30, 77]);
	});

	it('does not touch alpha at full intensity', () => {
		const image = { data: new Uint8ClampedArray([10, 20, 30, 77]), width: 1, height: 1 };
		applyFilter(image, { steps: [{ op: 'invert' }] }, 100);
		expect(Array.from(image.data)).toEqual([245, 235, 225, 77]);
	});

	it('skips disabled steps and splits colour from spatial', () => {
		const steps: FilterStep[] = [
			{ op: 'invert', enabled: false },
			{ op: 'tone', brightness: 5 },
			{ op: 'vignette', amount: 10 },
			{ op: 'nope' }
		];
		const { colour, spatial } = splitSteps(steps);
		expect(colour.map((s) => s.op)).toEqual(['tone']);
		expect(spatial.map((s) => s.op)).toEqual(['vignette']);
	});
});

describe('plugin ops', () => {
	const warm: OpSpec = {
		id: 'demo-pack.warmth',
		label: 'Warmth',
		kind: 'colour',
		source: 'plugin',
		plugin_id: 'demo-pack',
		params: [
			{ id: 'amount', label: 'Amount', type: 'int', min: 0, max: 100, default: 0, unit: null }
		]
	};

	it('folds a plugin colour map into the LUT', () => {
		const extensions = {
			ops: { [warm.id]: warm },
			colour: {
				[warm.id]: (rgb: [number, number, number], values: Record<string, unknown>) =>
					[rgb[0] + (values.amount as number) / 200, rgb[1], rgb[2]] as [number, number, number]
			}
		};
		const lut = compileLut([{ op: warm.id, amount: 20 }], null, 100, extensions);
		const reference = identityLut();
		expect(Math.abs(lut.data[3 * 1 + 0] - Math.min(1, reference.data[3] + 0.1))).toBeLessThan(1e-6);
	});

	it('refuses a plugin op nobody provided', () => {
		expect(() => compileLut([{ op: warm.id }], null, 100, { ops: { [warm.id]: warm } })).toThrow();
	});
});
