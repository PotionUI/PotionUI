import { describe, expect, it } from 'vitest';
import {
	countEdits,
	editedSteps,
	isStepEnabled,
	opLookup,
	serializeSteps,
	setStepParam,
	stepsEqual,
	toggleStep
} from './steps';
import { curvePath, parsePoints, sampleCurve } from './curve';

const lookup = opLookup();
const BASE = [
	{ op: 'white_balance', temperature: 34, tint: 6 },
	{ op: 'tone', contrast: 10 },
	{ op: 'vignette', amount: 22 }
];

describe('step edits', () => {
	it('counts changed params and toggled steps against the recipe', () => {
		expect(countEdits(BASE, BASE, lookup)).toBe(0);
		let steps = setStepParam(BASE, 0, 'temperature', 44);
		steps = setStepParam(steps, 1, 'saturation', 5);
		steps = toggleStep(steps, 2);
		expect(countEdits(BASE, steps, lookup)).toBe(3);
		expect(editedSteps(BASE, steps, lookup)).toBe(3);
	});

	it('treats an omitted param as its default', () => {
		const steps = setStepParam(BASE, 1, 'saturation', 0);
		expect(countEdits(BASE, steps, lookup)).toBe(0);
	});

	it('does not mutate the source steps', () => {
		setStepParam(BASE, 0, 'temperature', 1);
		toggleStep(BASE, 0);
		expect(BASE[0]).toEqual({ op: 'white_balance', temperature: 34, tint: 6 });
	});
});

describe('serialising', () => {
	it('drops enabled: true and keeps enabled: false', () => {
		const steps = toggleStep(toggleStep(BASE, 0), 0);
		expect(isStepEnabled(steps[0])).toBe(true);
		expect(serializeSteps(steps)).toEqual(BASE);
		expect(serializeSteps(toggleStep(BASE, 2))[2]).toEqual({ op: 'vignette', amount: 22, enabled: false });
		expect(stepsEqual(steps, BASE)).toBe(true);
	});
});

describe('curve plot maths', () => {
	it('parses point lists and rejects malformed ones', () => {
		expect(parsePoints([[0, 0], [1, 1]])).toEqual([[0, 0], [1, 1]]);
		expect(parsePoints([[0, 0]])).toBeNull();
		expect(parsePoints('x')).toBeNull();
		expect(parsePoints([[0, 'a'], [1, 1]])).toBeNull();
	});

	it('passes through its points and stays monotone', () => {
		const points: Array<[number, number]> = [[0, 0.02], [0.25, 0.22], [0.75, 0.8], [1, 0.98]];
		for (const [x, y] of points) expect(sampleCurve(points, x)).toBeCloseTo(y, 6);
		let last = -1;
		for (let i = 0; i <= 20; i++) {
			const value = sampleCurve(points, i / 20);
			expect(value).toBeGreaterThanOrEqual(last);
			last = value;
		}
	});

	it('builds a path that starts left and ends right', () => {
		const path = curvePath([[0, 0], [1, 1]], 160, 80, 4);
		expect(path.startsWith('M0.0 80.0')).toBe(true);
		expect(path.endsWith('L160.0 0.0')).toBe(true);
	});
});
