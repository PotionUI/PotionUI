import { describe, it, expect } from 'vitest';
import {
	clampStrength,
	parseStrengthInput,
	nudgeStrength,
	toggleLoraStrength,
	isLoraRowDisabled,
	formatStrength
} from './loraStrength';

describe('formatStrength', () => {
	it('shows two decimals for common values', () => {
		expect(formatStrength(1)).toBe('1.00');
		expect(formatStrength(0.5)).toBe('0.50');
		expect(formatStrength(-0.7)).toBe('-0.70');
	});

	it('shows exact value when precision exceeds two decimals', () => {
		expect(formatStrength(0.125)).toBe('0.125');
		expect(formatStrength(0.004)).toBe('0.004');
	});
});

describe('clampStrength', () => {
	it('passes values inside the range through unchanged', () => {
		expect(clampStrength(0.5, -2, 2)).toBe(0.5);
	});

	it('clamps to min/max', () => {
		expect(clampStrength(-5, -2, 2)).toBe(-2);
		expect(clampStrength(5, -2, 2)).toBe(2);
	});

	it('never rounds to a step', () => {
		expect(clampStrength(0.04, -2, 2)).toBe(0.04);
	});
});

describe('parseStrengthInput', () => {
	it('accepts exact typed precision even on a coarse-step field', () => {
		expect(parseStrengthInput('0.04', -2, 2)).toBe(0.04);
	});

	it('clamps out-of-range typed values instead of rejecting them', () => {
		expect(parseStrengthInput('20', -15, 15)).toBe(15);
		expect(parseStrengthInput('-20', -15, 15)).toBe(-15);
	});

	it('returns null for non-numeric input', () => {
		expect(parseStrengthInput('abc', -2, 2)).toBeNull();
		expect(parseStrengthInput('', -2, 2)).toBeNull();
	});

	it('accepts negative strengths (inverted LoRA)', () => {
		expect(parseStrengthInput('-1.5', -2, 2)).toBe(-1.5);
	});
});

describe('nudgeStrength', () => {
	it('nudges by the base step on a plain click', () => {
		expect(nudgeStrength(1, 1, { large: false, min: -2, max: 2 })).toBe(1.05);
		expect(nudgeStrength(1, -1, { large: false, min: -2, max: 2 })).toBe(0.95);
	});

	it('nudges by the large step on a shift-click', () => {
		expect(nudgeStrength(1, 1, { large: true, min: -2, max: 2 })).toBe(1.25);
	});

	it('clamps at the range boundary instead of overshooting', () => {
		expect(nudgeStrength(1.98, 1, { large: false, min: -2, max: 2 })).toBe(2);
		expect(nudgeStrength(-1.98, -1, { large: false, min: -2, max: 2 })).toBe(-2);
	});

	it('does not accumulate floating point drift across repeated nudges', () => {
		let value = 0.1;
		for (let i = 0; i < 5; i++) {
			value = nudgeStrength(value, 1, { large: false, min: -2, max: 2 });
		}
		expect(value).toBe(0.35);
	});
});

describe('toggleLoraStrength', () => {
	it('turning off zeroes the strength and remembers the prior value on the row', () => {
		expect(toggleLoraStrength({ model: 'model:a', strength: 0.7 }, 1)).toEqual({
			model: 'model:a',
			strength: 0,
			saved_strength: 0.7
		});
	});

	it('turning on restores the remembered strength and drops saved_strength', () => {
		expect(toggleLoraStrength({ model: 'model:a', strength: 0, saved_strength: 0.7 }, 1)).toEqual({
			model: 'model:a',
			strength: 0.7
		});
	});

	// Regression coverage: dragging the strength slider through zero must
	// NOT read as "already disabled". A row
	// with no `saved_strength` is enabled regardless of its live strength -
	// including exactly 0, which is a perfectly normal value reached by a
	// live drag, a nudge, or a typed "0" (never by the toggle itself).
	it('a row resting at live zero with no toggle history is enabled - toggling it off remembers the zero', () => {
		expect(isLoraRowDisabled({ model: 'model:a', strength: 0 })).toBe(false);
		expect(toggleLoraStrength({ model: 'model:a', strength: 0 }, 1)).toEqual({
			model: 'model:a',
			strength: 0,
			saved_strength: 0
		});
	});

	it('turning on falls back to 1 when both the remembered value and the default are zero', () => {
		expect(toggleLoraStrength({ model: 'model:a', strength: 0, saved_strength: 0 }, 0)).toEqual({
			model: 'model:a',
			strength: 1
		});
	});

	it('treats a remembered zero as "nothing remembered" and uses the default instead', () => {
		expect(toggleLoraStrength({ model: 'model:a', strength: 0, saved_strength: 0 }, 1)).toEqual({
			model: 'model:a',
			strength: 1
		});
	});

	it('remembers negative strengths (inverted LoRA) correctly', () => {
		expect(toggleLoraStrength({ model: 'model:a', strength: -0.8 }, 1)).toEqual({
			model: 'model:a',
			strength: 0,
			saved_strength: -0.8
		});
		expect(toggleLoraStrength({ model: 'model:a', strength: 0, saved_strength: -0.8 }, 1)).toEqual({
			model: 'model:a',
			strength: -0.8
		});
	});

	it('directly editing strength while enabled never carries a stale saved_strength forward, and stays enabled through every value including zero', () => {
		// This is enforced by updateStrength() in LoraPickerField.svelte (it
		// always rebuilds {model, strength} from scratch), not by this
		// function - documented here as the invariant the two rely on together.
		const edited = { model: 'model:a', strength: 1.5 };
		expect(edited).not.toHaveProperty('saved_strength');
		expect(isLoraRowDisabled(edited)).toBe(false);

		// Same invariant at the exact value that used to trip the bug.
		const editedToZero = { model: 'model:a', strength: 0 };
		expect(editedToZero).not.toHaveProperty('saved_strength');
		expect(isLoraRowDisabled(editedToZero)).toBe(false);

		// ...and through a negative value (inverted LoRA), which must be just
		// as smooth a pass-through as landing on zero.
		const editedNegative = { model: 'model:a', strength: -0.4 };
		expect(isLoraRowDisabled(editedNegative)).toBe(false);
	});

	it('round-trips through JSON (session persistence) without losing saved_strength', () => {
		const off = toggleLoraStrength({ model: 'model:a', strength: 0.7 }, 1);
		const persisted = JSON.parse(JSON.stringify(off));
		expect(persisted).toEqual({ model: 'model:a', strength: 0, saved_strength: 0.7 });
		expect(isLoraRowDisabled(persisted)).toBe(true);

		const restored = toggleLoraStrength(persisted, 1);
		expect(restored).toEqual({ model: 'model:a', strength: 0.7 });
		expect(isLoraRowDisabled(restored)).toBe(false);
	});

	it('round-trips a row with no memory (never toggled off) the same way', () => {
		const row = { model: 'model:a', strength: 1.2 };
		const persisted = JSON.parse(JSON.stringify(row));
		expect(persisted).toEqual(row);
		expect(isLoraRowDisabled(persisted)).toBe(false);
	});
});

describe('isLoraRowDisabled', () => {
	it('is false when saved_strength is absent, at any strength value', () => {
		expect(isLoraRowDisabled({ model: 'model:a', strength: 1 })).toBe(false);
		expect(isLoraRowDisabled({ model: 'model:a', strength: 0 })).toBe(false);
		expect(isLoraRowDisabled({ model: 'model:a', strength: -0.8 })).toBe(false);
	});

	it('is true whenever saved_strength is present, even when its value is itself zero', () => {
		expect(isLoraRowDisabled({ model: 'model:a', strength: 0, saved_strength: 0.7 })).toBe(true);
		expect(isLoraRowDisabled({ model: 'model:a', strength: 0, saved_strength: 0 })).toBe(true);
		expect(isLoraRowDisabled({ model: 'model:a', strength: 0, saved_strength: -0.8 })).toBe(true);
	});
});
