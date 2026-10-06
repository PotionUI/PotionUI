import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { LutMemo, renderRecipe } from './render';
import type { FilterStep } from './engine';

interface GoldenCase {
	name: string;
	filter: string;
	steps: FilterStep[];
	intensity: number;
	expected: number[];
}

interface GoldenFile {
	tolerance: number;
	image: { width: number; height: number; pixels: number[] };
	cases: GoldenCase[];
}

const golden = JSON.parse(
	readFileSync(
		fileURLToPath(new URL('../../../../tests/fixtures/filters/golden/filters.json', import.meta.url)),
		'utf-8'
	)
) as GoldenFile;

function buffer() {
	const { width, height, pixels } = golden.image;
	const data = new Uint8ClampedArray(width * height * 4);
	for (let i = 0; i < width * height; i++) {
		data[i * 4] = pixels[i * 3];
		data[i * 4 + 1] = pixels[i * 3 + 1];
		data[i * 4 + 2] = pixels[i * 3 + 2];
		data[i * 4 + 3] = 255;
	}
	return { width, height, data };
}

function maxDiff(data: Uint8ClampedArray, expected: number[]): number {
	let worst = 0;
	for (let i = 0; i < expected.length / 3; i++) {
		for (let c = 0; c < 3; c++) worst = Math.max(worst, Math.abs(data[i * 4 + c] - expected[i * 3 + c]));
	}
	return worst;
}

function goldenCase(name: string): GoldenCase {
	const found = golden.cases.find((entry) => entry.name === name);
	if (!found) throw new Error(`missing golden case ${name}`);
	return found;
}

describe('renderRecipe on the real engine', () => {
	for (const testCase of golden.cases) {
		it(`matches the golden ${testCase.name} with a prebuilt LUT`, () => {
			const source = buffer();
			const before = new Uint8ClampedArray(source.data);
			const recipe = { steps: testCase.steps };
			const lut = new LutMemo().get(recipe, testCase.intensity);
			const output = renderRecipe(source, recipe, testCase.intensity, { lut });
			expect(maxDiff(output.data, testCase.expected)).toBeLessThanOrEqual(golden.tolerance);
			expect(source.data).toEqual(before);
		});
	}

	it('takes intensity as 0..100, so Ember at 50 is the 50 golden and not a no-op', () => {
		const ember = goldenCase('ember-50');
		const output = renderRecipe(buffer(), { steps: ember.steps }, 50);
		expect(maxDiff(output.data, ember.expected)).toBeLessThanOrEqual(golden.tolerance);
		expect(maxDiff(output.data, goldenCase('ember-0').expected)).toBeGreaterThan(golden.tolerance);
	});
});

describe('LutMemo', () => {
	const ember = goldenCase('ember-100');

	it('reuses the LUT while only spatial params change', () => {
		const memo = new LutMemo();
		const first = memo.get({ steps: ember.steps }, 70);
		const spatialEdit = ember.steps.map((step) => (step.op === 'vignette' ? { ...step, amount: 5 } : step));
		expect(memo.get({ steps: spatialEdit }, 70)).toBe(first);
	});

	it('recompiles when the intensity or a colour step changes', () => {
		const memo = new LutMemo();
		const first = memo.get({ steps: ember.steps }, 70);
		const second = memo.get({ steps: ember.steps }, 71);
		expect(second).not.toBe(first);
		const colourEdit = ember.steps.map((step) =>
			step.op === 'white_balance' ? { ...step, temperature: 0 } : step
		);
		expect(memo.get({ steps: colourEdit }, 71)).not.toBe(second);
	});
});
