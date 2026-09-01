import { describe, it, expect } from 'vitest';
import type { LoraPickerItem } from '$lib/types/models';
import {
	clearStepWindow,
	describeStepWindow,
	hasStepWindow,
	parseStepInput,
	setStepBound,
	stepBound
} from './loraStepWindow';

const row = (over: Partial<LoraPickerItem> = {}): LoraPickerItem => ({
	model: 'model:abc',
	strength: 1,
	...over
});

describe('stepBound', () => {
	it('reads a set bound', () => {
		expect(stepBound(row({ step_start: 3 }), 'step_start')).toBe(3);
	});

	it('treats absent, null and non-positive as unset', () => {
		expect(stepBound(row(), 'step_start')).toBeNull();
		expect(stepBound(row({ step_end: null }), 'step_end')).toBeNull();
		expect(stepBound(row({ step_start: 0 }), 'step_start')).toBeNull();
	});
});

describe('hasStepWindow', () => {
	it('is false for a plain row', () => {
		expect(hasStepWindow(row())).toBe(false);
	});

	it('is true when either bound is set', () => {
		expect(hasStepWindow(row({ step_end: 2 }))).toBe(true);
		expect(hasStepWindow(row({ step_start: 3 }))).toBe(true);
	});
});

describe('describeStepWindow', () => {
	it('describes the motivating case', () => {
		expect(describeStepWindow(row({ step_start: 1, step_end: 2 }))).toBe('Steps 1–2');
	});

	it('collapses a single-step window', () => {
		expect(describeStepWindow(row({ step_start: 4, step_end: 4 }))).toBe('Step 4');
	});

	it('describes each half-open form', () => {
		expect(describeStepWindow(row({ step_start: 3 }))).toBe('From step 3');
		expect(describeStepWindow(row({ step_end: 2 }))).toBe('Through step 2');
	});

	it('is empty with no window', () => {
		expect(describeStepWindow(row())).toBe('');
	});
});

describe('parseStepInput', () => {
	it('parses a step number', () => {
		expect(parseStepInput('2')).toBe(2);
		expect(parseStepInput('  8 ')).toBe(8);
	});

	it('clears on blank', () => {
		expect(parseStepInput('')).toBeNull();
		expect(parseStepInput('   ')).toBeNull();
	});

	it('reverts (undefined) rather than clearing on junk', () => {
		expect(parseStepInput('two')).toBeUndefined();
		expect(parseStepInput('-1')).toBeUndefined();
		expect(parseStepInput('1.5')).toBeUndefined();
		expect(parseStepInput('0')).toBeUndefined();
	});
});

describe('setStepBound', () => {
	it('sets a bound and leaves other keys untouched', () => {
		const next = setStepBound(row({ strength: 0.8, saved_strength: 0.8 }), 'step_end', 2);
		expect(next).toEqual({ model: 'model:abc', strength: 0.8, saved_strength: 0.8, step_end: 2 });
	});

	it('removes the key when cleared, rather than writing null', () => {
		const next = setStepBound(row({ step_start: 1, step_end: 2 }), 'step_end', null);
		expect('step_end' in next).toBe(false);
		expect(next.step_start).toBe(1);
	});

	it('does not mutate the input row', () => {
		const original = row({ step_end: 2 });
		setStepBound(original, 'step_end', 5);
		expect(original.step_end).toBe(2);
	});

	it('raises an end typed below the start, without moving the start', () => {
		const next = setStepBound(row({ step_start: 4 }), 'step_end', 2);
		expect(next.step_end).toBe(4);
		expect(next.step_start).toBe(4);
	});

	it('lowers a start typed above the end, without moving the end', () => {
		const next = setStepBound(row({ step_end: 3 }), 'step_start', 7);
		expect(next.step_start).toBe(3);
		expect(next.step_end).toBe(3);
	});

	it('floors to the first step', () => {
		expect(setStepBound(row(), 'step_start', 0).step_start).toBe(1);
	});
});

describe('clearStepWindow', () => {
	it('drops both keys and keeps the rest', () => {
		const next = clearStepWindow(row({ strength: 0.5, step_start: 1, step_end: 2 }));
		expect(next).toEqual({ model: 'model:abc', strength: 0.5 });
	});
});
