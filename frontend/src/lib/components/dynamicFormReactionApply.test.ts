import { describe, it, expect } from 'vitest';
import { applyReactionValueChanges, valuesEqual } from './dynamicFormReactionApply';

describe('valuesEqual', () => {
	it('treats identical scalars as equal', () => {
		expect(valuesEqual(1, 1)).toBe(true);
		expect(valuesEqual('a', 'a')).toBe(true);
		expect(valuesEqual(true, true)).toBe(true);
		expect(valuesEqual(null, null)).toBe(true);
		expect(valuesEqual(undefined, undefined)).toBe(true);
	});

	it('treats different scalars as unequal', () => {
		expect(valuesEqual(1, 2)).toBe(false);
		expect(valuesEqual('a', 'b')).toBe(false);
		expect(valuesEqual(0, false)).toBe(false); // no coercion
	});

	it('deep-compares arrays/objects instead of using reference equality', () => {
		// This is the exact shape that breaks with `!==`: two distinct array
		// references (as produced by a fresh JSON.parse(JSON.stringify(...))
		// deep-clone every reprocess) that are nonetheless content-equal.
		expect(valuesEqual([1, 2, 3], [1, 2, 3])).toBe(true);
		expect(valuesEqual({ a: 1, b: 2 }, { a: 1, b: 2 })).toBe(true);
	});

	it('detects real differences inside arrays/objects', () => {
		expect(valuesEqual([1, 2, 3], [1, 2, 4])).toBe(false);
		expect(valuesEqual({ a: 1 }, { a: 2 })).toBe(false);
	});
});

describe('applyReactionValueChanges', () => {
	it('applies a set_value correction with no trigger-cache priming required', () => {
		// Regression case: previously, a reaction's set_value correction only
		// reached formData if a "tracked" trigger field's value differed from a
		// lastTriggerValues cache primed on a prior run. Here formData already
		// has quality_mode=true baked in from the schema defaults (nothing
		// "changed" from any prior snapshot) and cfg is still stale at 7 - the
		// correction must apply on the very first reprocess.
		const formData = { quality_mode: true, cfg: 7 };
		const valueChanges = { cfg: 1 };

		const result = applyReactionValueChanges(formData, valueChanges);

		expect(result.changed).toBe(true);
		expect(result.data.cfg).toBe(1);
		expect(result.data.quality_mode).toBe(true);
	});

	it('is a no-op when every proposed value already matches formData', () => {
		const formData = { cfg: 1, sampler: 'EULER_A' };
		const valueChanges = { cfg: 1 };

		const result = applyReactionValueChanges(formData, valueChanges);

		expect(result.changed).toBe(false);
		expect(result.data).toBe(formData); // same reference - no reactive re-trigger
	});

	it('does not loop on repeated reprocess with unchanged values, including object/array set_value', () => {
		const formData = { tags: ['a', 'b'] };
		const valueChanges = { tags: ['a', 'b'] }; // fresh array reference, same content

		const first = applyReactionValueChanges(formData, valueChanges);
		expect(first.changed).toBe(false);
		expect(first.data).toBe(formData);

		// Simulate a second reprocess against whatever the caller now holds -
		// still a no-op, proving convergence rather than perpetual reassignment.
		const second = applyReactionValueChanges(first.data, { tags: ['a', 'b'] });
		expect(second.changed).toBe(false);
		expect(second.data).toBe(first.data);
	});

	it('converges in one corrective step even for a fresh object/array set_value reference', () => {
		const formData = { limits: { min: 0, max: 10 } };
		// First reprocess: the reaction's set_value differs in content.
		const applied = applyReactionValueChanges(formData, { limits: { min: 1, max: 10 } });
		expect(applied.changed).toBe(true);
		expect(applied.data.limits).toEqual({ min: 1, max: 10 });

		// Second reprocess against a brand-new but content-equal object
		// (as processSchemaWithReactions' deep-clone would produce): must settle.
		const settled = applyReactionValueChanges(applied.data, { limits: { min: 1, max: 10 } });
		expect(settled.changed).toBe(false);
		expect(settled.data).toBe(applied.data);
	});

	it('leaves untouched fields alone', () => {
		const formData = { a: 1, b: 2, c: 3 };
		const result = applyReactionValueChanges(formData, { b: 99 });

		expect(result.data).toEqual({ a: 1, b: 99, c: 3 });
	});
});
