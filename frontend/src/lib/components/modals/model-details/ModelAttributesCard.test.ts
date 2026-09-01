import { describe, expect, it } from 'vitest';
import {
	coerceAttributeInput,
	definitionsForModelType,
	extractUpdatedSharedMetadata,
	extractUpdatedUserMetadata,
	formatAttributeValue,
	inputConfigForAttribute,
	resolveEffectiveAttributeValue
} from './ModelAttributesCard';
import type { AttributeDefinition } from '$lib/types/models';

function definition(overrides: Partial<AttributeDefinition> = {}): AttributeDefinition {
	return {
		id: 'd1',
		key: 'strength',
		label: 'Strength',
		field_type: 'slider',
		model_types: ['lora'],
		config: { min: 0, max: 2, step: 0.05 },
		default_value: 1.0,
		description: 'Default strength applied when this LoRA is added to a generation',
		per_user: false,
		admin_only: false,
		system: true,
		source: 'core',
		...overrides
	};
}

describe('inputConfigForAttribute', () => {
	it('maps slider fields to a number input carrying min/max/step', () => {
		expect(inputConfigForAttribute(definition())).toEqual({
			type: 'number',
			min: 0,
			max: 2,
			step: 0.05
		});
	});

	it('maps number fields to a number input the same way as slider', () => {
		expect(inputConfigForAttribute(definition({ field_type: 'number', config: {} }))).toEqual({
			type: 'number',
			min: undefined,
			max: undefined,
			step: undefined
		});
	});

	it('maps text fields to a text input', () => {
		expect(inputConfigForAttribute(definition({ field_type: 'text', config: {} }))).toEqual({
			type: 'text'
		});
	});

	it('maps checkbox fields to a checkbox', () => {
		expect(inputConfigForAttribute(definition({ field_type: 'checkbox', config: {} }))).toEqual({
			type: 'checkbox'
		});
	});

	it('maps select fields to their declared options', () => {
		const options = [{ value: 'a', label: 'A' }];
		expect(inputConfigForAttribute(definition({ field_type: 'select', config: { options } }))).toEqual({
			type: 'select',
			options
		});
	});

	it('maps tags fields to a chip editor', () => {
		expect(inputConfigForAttribute(definition({ field_type: 'tags', config: {} }))).toEqual({
			type: 'tags'
		});
	});

	it('maps range fields to a range input carrying min/max/step', () => {
		expect(
			inputConfigForAttribute(definition({ field_type: 'range', config: { min: -2, max: 2, step: 0.05 } }))
		).toEqual({ type: 'range', min: -2, max: 2, step: 0.05 });
	});
});

describe('definitionsForModelType', () => {
	it('includes a definition with no declared model_types for every type', () => {
		const defs = [definition({ model_types: [] })];
		expect(definitionsForModelType(defs, 'checkpoint')).toEqual(defs);
	});

	it('excludes a definition scoped to a different model type', () => {
		const defs = [definition({ model_types: ['checkpoint'] })];
		expect(definitionsForModelType(defs, 'lora')).toEqual([]);
	});

	it('includes a definition whose model_types lists the given type', () => {
		const defs = [definition({ model_types: ['lora', 'checkpoint'] })];
		expect(definitionsForModelType(defs, 'lora')).toEqual(defs);
	});
});

describe('resolveEffectiveAttributeValue', () => {
	it('prefers the user overlay when present', () => {
		expect(resolveEffectiveAttributeValue(definition(), { strength: 0.7 }, { strength: 1.5 })).toBe(1.5);
	});

	it('falls back to the shared value when there is no user overlay', () => {
		expect(resolveEffectiveAttributeValue(definition(), { strength: 0.7 }, {})).toBe(0.7);
		expect(resolveEffectiveAttributeValue(definition(), { strength: 0.7 }, null)).toBe(0.7);
	});

	it('falls back to the definition default when neither layer has a value', () => {
		expect(resolveEffectiveAttributeValue(definition(), {}, {})).toBe(1.0);
		expect(resolveEffectiveAttributeValue(definition(), null, null)).toBe(1.0);
	});

	it('keeps an explicit falsy value (0) at every layer rather than falling through', () => {
		expect(resolveEffectiveAttributeValue(definition(), { strength: 0 }, {})).toBe(0);
		expect(resolveEffectiveAttributeValue(definition(), { strength: 0.7 }, { strength: 0 })).toBe(0);
	});
});

describe('coerceAttributeInput', () => {
	it('parses a slider/number field to a number', () => {
		expect(coerceAttributeInput(definition(), '0.65')).toBe(0.65);
		expect(coerceAttributeInput(definition({ field_type: 'number' }), '4')).toBe(4);
	});

	it('falls back to the definition default on unparsable numeric input', () => {
		expect(coerceAttributeInput(definition(), 'not-a-number')).toBe(1.0);
	});

	it('passes a text field through as a string', () => {
		expect(coerceAttributeInput(definition({ field_type: 'text' }), 'hello')).toBe('hello');
	});

	it('coerces a checkbox field to a real boolean', () => {
		expect(coerceAttributeInput(definition({ field_type: 'checkbox' }), true)).toBe(true);
		expect(coerceAttributeInput(definition({ field_type: 'checkbox' }), false)).toBe(false);
	});

	it('passes a tags field through as an array, defaulting to empty', () => {
		expect(coerceAttributeInput(definition({ field_type: 'tags' }), ['a', 'b'])).toEqual(['a', 'b']);
		expect(coerceAttributeInput(definition({ field_type: 'tags' }), 'not-an-array' as any)).toEqual([]);
	});

	it('coerces a range field two-element string tuple to a sorted [lo, hi] pair', () => {
		const def = definition({ field_type: 'range', config: { min: -2, max: 2, step: 0.05 } });
		expect(coerceAttributeInput(def, ['0.7', '1'])).toEqual([0.7, 1]);
		expect(coerceAttributeInput(def, ['1', '0.7'])).toEqual([0.7, 1]);
	});

	it('coerces a range field with only one filled input to a degenerate range', () => {
		const def = definition({ field_type: 'range', config: { min: -2, max: 2, step: 0.05 } });
		expect(coerceAttributeInput(def, ['0.8', ''])).toEqual([0.8, 0.8]);
		expect(coerceAttributeInput(def, ['', '0.8'])).toEqual([0.8, 0.8]);
	});

	it('coerces an empty/unparseable range buffer to null, not a garbage range', () => {
		const def = definition({ field_type: 'range', config: { min: -2, max: 2, step: 0.05 } });
		expect(coerceAttributeInput(def, ['', ''])).toBeNull();
		expect(coerceAttributeInput(def, [])).toBeNull();
	});
});

describe('formatAttributeValue', () => {
	it('renders checkboxes as Yes/No', () => {
		const def = definition({ field_type: 'checkbox' });
		expect(formatAttributeValue(def, true)).toBe('Yes');
		expect(formatAttributeValue(def, false)).toBe('No');
	});

	it('renders a missing value as an em dash', () => {
		expect(formatAttributeValue(definition(), null)).toBe('—');
		expect(formatAttributeValue(definition(), undefined)).toBe('—');
	});

	it('renders tags joined, or an em dash when empty', () => {
		const def = definition({ field_type: 'tags' });
		expect(formatAttributeValue(def, ['a', 'b'])).toBe('a, b');
		expect(formatAttributeValue(def, [])).toBe('—');
	});

	it('renders a select value by its option label, not its raw value', () => {
		const def = definition({ field_type: 'select', config: { options: [{ value: 'v1', label: 'Version 1' }] } });
		expect(formatAttributeValue(def, 'v1')).toBe('Version 1');
	});

	it('stringifies any other value', () => {
		expect(formatAttributeValue(definition(), 0.7)).toBe('0.7');
	});

	it('renders a range value formatted with an en dash', () => {
		const def = definition({ field_type: 'range', config: { min: -2, max: 2, step: 0.05 } });
		expect(formatAttributeValue(def, [0.7, 1])).toBe('0.70–1.00');
	});

	it('renders a degenerate range as a single number', () => {
		const def = definition({ field_type: 'range', config: { min: -2, max: 2, step: 0.05 } });
		expect(formatAttributeValue(def, [1, 1])).toBe('1.00');
	});

	it('renders a not-set range as an em dash', () => {
		const def = definition({ field_type: 'range', config: { min: -2, max: 2, step: 0.05 } });
		expect(formatAttributeValue(def, null)).toBe('—');
	});
});

describe('extractUpdatedSharedMetadata', () => {
	it('reads the server-coerced values from the nested model, not the response root', () => {
		const responseData = { model: { model_metadata: { strength: 0.75 } } };
		expect(extractUpdatedSharedMetadata(responseData, { strength: 0.7 })).toEqual({ strength: 0.75 });
	});

	it('falls back to the locally-computed values when the response has no model', () => {
		expect(extractUpdatedSharedMetadata(undefined, { strength: 0.7 })).toEqual({ strength: 0.7 });
		expect(extractUpdatedSharedMetadata({}, { strength: 0.7 })).toEqual({ strength: 0.7 });
	});
});

describe('extractUpdatedUserMetadata', () => {
	it('reads the server-coerced values flat off data.values', () => {
		const responseData = { values: { strength: 1.25 } };
		expect(extractUpdatedUserMetadata(responseData, { strength: 1.5 })).toEqual({ strength: 1.25 });
	});

	it('falls back to the locally-computed values when the response has none', () => {
		expect(extractUpdatedUserMetadata(undefined, { strength: 1.5 })).toEqual({ strength: 1.5 });
	});
});
