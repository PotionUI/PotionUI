import { describe, expect, it } from 'vitest';
import {
	applyAction,
	buildDependencyMap,
	evaluateCondition,
	extractAllFields,
	extractFieldDependencies,
	getFieldsThatTriggerReactions,
	operators,
	processAllFieldReactions,
	processFieldReactions,
	processSchemaWithReactions,
	type Action,
	type Condition,
	type FieldConfig,
	type LogicalCondition
} from './reactions';

describe('operators', () => {
	it('the registry exposes exactly the closed 12-operator set', () => {
		const expected = [
			'equals',
			'not_equals',
			'in',
			'not_in',
			'greater_than',
			'less_than',
			'greater_than_or_equals',
			'less_than_or_equals',
			'contains',
			'not_contains',
			'is_empty',
			'is_not_empty'
		];
		expect(Object.keys(operators).sort()).toEqual(expected.sort());
	});

	it('equals', () => {
		expect(operators.equals('a', 'a')).toBe(true);
		expect(operators.equals('a', 'b')).toBe(false);
		expect(operators.equals(5, 5)).toBe(true);
		expect(operators.equals(true, true)).toBe(true);
		expect(operators.equals(true, false)).toBe(false);
	});

	it('not_equals', () => {
		expect(operators.not_equals('a', 'b')).toBe(true);
		expect(operators.not_equals('a', 'a')).toBe(false);
	});

	it('in', () => {
		expect(operators.in('a', ['a', 'b'])).toBe(true);
		expect(operators.in('c', ['a', 'b'])).toBe(false);
		expect(operators.in('a', 'not-an-array')).toBe(false);
	});

	it('not_in', () => {
		expect(operators.not_in('c', ['a', 'b'])).toBe(true);
		expect(operators.not_in('a', ['a', 'b'])).toBe(false);
		// Unlike `in`, a non-array condition value short-circuits to false (not true) here -
		// this mirrors the exact moved implementation, not the backend's dead engine.
		expect(operators.not_in('a', 'not-an-array')).toBe(false);
	});

	it('greater_than', () => {
		expect(operators.greater_than(5, 3)).toBe(true);
		expect(operators.greater_than(3, 5)).toBe(false);
		expect(operators.greater_than('abc', 5)).toBe(false);
		expect(operators.greater_than(null, 5)).toBe(false);
		// Numeric strings are coerced before comparison.
		expect(operators.greater_than('10', '5')).toBe(true);
		expect(operators.greater_than('5', '10')).toBe(false);
	});

	it('less_than', () => {
		expect(operators.less_than(3, 5)).toBe(true);
		expect(operators.less_than(5, 3)).toBe(false);
		expect(operators.less_than('abc', 5)).toBe(false);
	});

	it('greater_than_or_equals', () => {
		expect(operators.greater_than_or_equals(5, 5)).toBe(true);
		expect(operators.greater_than_or_equals(4, 5)).toBe(false);
	});

	it('less_than_or_equals', () => {
		expect(operators.less_than_or_equals(5, 5)).toBe(true);
		expect(operators.less_than_or_equals(6, 5)).toBe(false);
	});

	it('contains', () => {
		expect(operators.contains('hello world', 'world')).toBe(true);
		expect(operators.contains('hello world', 'xyz')).toBe(false);
		// Non-string field/condition values are stringified before comparison.
		expect(operators.contains(12345, '234')).toBe(true);
		expect(operators.contains(12345, '678')).toBe(false);
	});

	it('not_contains', () => {
		expect(operators.not_contains('hello world', 'xyz')).toBe(true);
		expect(operators.not_contains('hello world', 'world')).toBe(false);
	});

	it('is_empty', () => {
		expect(operators.is_empty(null)).toBe(true);
		expect(operators.is_empty(undefined)).toBe(true);
		expect(operators.is_empty('')).toBe(true);
		expect(operators.is_empty([])).toBe(true);
		expect(operators.is_empty({})).toBe(true);
		expect(operators.is_empty('x')).toBe(false);
		expect(operators.is_empty([1])).toBe(false);
		expect(operators.is_empty({ a: 1 })).toBe(false);
		expect(operators.is_empty(0)).toBe(false);
	});

	it('is_not_empty', () => {
		expect(operators.is_not_empty(null)).toBe(false);
		expect(operators.is_not_empty('')).toBe(false);
		expect(operators.is_not_empty('x')).toBe(true);
		expect(operators.is_not_empty([1])).toBe(true);
		expect(operators.is_not_empty({ a: 1 })).toBe(true);
	});
});

describe('evaluateCondition', () => {
	it('evaluates a single sugar-form condition', () => {
		const when: Condition = { field: 'sampler', equals: 'EULER' };
		expect(evaluateCondition(when, { sampler: 'EULER' })).toBe(true);
		expect(evaluateCondition(when, { sampler: 'DPMPP' })).toBe(false);
	});

	it('evaluates false when the field is missing from formData', () => {
		const when: Condition = { field: 'sampler', equals: 'EULER' };
		expect(evaluateCondition(when, { steps: 10 })).toBe(false);
	});

	it('evaluates a single explicit operator/value condition', () => {
		const when: Condition = { field: 'steps', operator: 'greater_than', value: 10 };
		expect(evaluateCondition(when, { steps: 20 })).toBe(true);
		expect(evaluateCondition(when, { steps: 5 })).toBe(false);
	});

	it('treats a list of conditions as implicit AND', () => {
		const when: Condition[] = [
			{ field: 'sampler', equals: 'EULER' },
			{ field: 'steps', greater_than: 10 }
		];
		expect(evaluateCondition(when, { sampler: 'EULER', steps: 20 })).toBe(true);
		expect(evaluateCondition(when, { sampler: 'EULER', steps: 5 })).toBe(false);
		expect(evaluateCondition(when, { sampler: 'DPMPP', steps: 20 })).toBe(false);
	});

	it('evaluates explicit AND logical conditions', () => {
		const when: LogicalCondition = {
			logic: 'AND',
			conditions: [
				{ field: 'a', equals: 1 },
				{ field: 'b', equals: 2 }
			]
		};
		expect(evaluateCondition(when, { a: 1, b: 2 })).toBe(true);
		expect(evaluateCondition(when, { a: 1, b: 3 })).toBe(false);
	});

	it('evaluates explicit OR logical conditions', () => {
		const when: LogicalCondition = {
			logic: 'OR',
			conditions: [
				{ field: 'a', equals: 1 },
				{ field: 'b', equals: 2 }
			]
		};
		expect(evaluateCondition(when, { a: 1, b: 999 })).toBe(true);
		expect(evaluateCondition(when, { a: 999, b: 2 })).toBe(true);
		expect(evaluateCondition(when, { a: 999, b: 999 })).toBe(false);
	});

	it('does not recurse into nested logical groups nested inside an implicit-AND list', () => {
		// The implicit-AND branch evaluates each list item as a single field condition
		// (not via evaluateCondition), so a nested {logic, conditions} item is not
		// itself evaluated as a group - this documents that boundary rather than papering
		// over it, matching the moved implementation exactly.
		const when: Condition[] = [
			{ field: 'mode', equals: 'advanced' },
			{ logic: 'OR', conditions: [{ field: 'a', equals: 1 }, { field: 'b', equals: 2 }] } as any
		];
		expect(evaluateCondition(when, { mode: 'advanced', a: 1, b: 999 })).toBe(false);
	});

	it('returns false for an unknown operator', () => {
		const when: Condition = { field: 'sampler', operator: 'bogus_op', value: 'x' };
		expect(evaluateCondition(when, { sampler: 'x' })).toBe(false);
	});

	it('treats an empty conditions list in a logical group as true', () => {
		const when: LogicalCondition = { logic: 'AND', conditions: [] };
		expect(evaluateCondition(when, {})).toBe(true);
	});
});

describe('applyAction', () => {
	const baseField: FieldConfig = { type: 'select', name: 'sampler' };

	it('applies set_visibility', () => {
		const result = applyAction(baseField, { set_visibility: false });
		expect(result.visible).toBe(false);
	});

	it('applies set_disabled', () => {
		const result = applyAction(baseField, { set_disabled: true });
		expect(result.disabled).toBe(true);
	});

	it('applies update_options', () => {
		const action: Action = { update_options: [{ label: 'A', value: 'a' }] };
		const result = applyAction({ ...baseField, configuration: { existing: true } }, action);
		expect(result.configuration).toEqual({ existing: true, options: [{ label: 'A', value: 'a' }] });
	});

	it('applies update_options when the field has no existing configuration', () => {
		const action: Action = { update_options: [{ label: 'New', value: 'new' }] };
		const result = applyAction(baseField, action);
		expect(result.configuration).toEqual({ options: [{ label: 'New', value: 'new' }] });
	});

	it('applies set_filter_tags, merging with existing configuration', () => {
		const action: Action = { set_filter_tags: ['tag_a', 'tag_b'] };
		const result = applyAction({ ...baseField, configuration: { model_type: 'checkpoint' } }, action);
		expect(result.configuration).toEqual({ model_type: 'checkpoint', filter_tags: ['tag_a', 'tag_b'] });
	});

	it('applies an explicit empty set_filter_tags list (not confused with null)', () => {
		const action: Action = { set_filter_tags: [] };
		const result = applyAction({ ...baseField, configuration: { filter_tags: ['stale'] } }, action);
		expect(result.configuration).toEqual({ filter_tags: [] });
	});

	it('applies update_validation, merging with existing validation', () => {
		const action: Action = { update_validation: { max: 100 } };
		const result = applyAction({ ...baseField, validation: { min: 0 } }, action);
		expect(result.validation).toEqual({ min: 0, max: 100 });
	});

	it('applies set_value and records it in valueChanges keyed by field name', () => {
		const valueChanges: Record<string, any> = {};
		const result = applyAction(baseField, { set_value: 'EULER' }, valueChanges);
		expect(result.value).toBe('EULER');
		expect(valueChanges).toEqual({ sampler: 'EULER' });
	});

	it('does not record set_value in valueChanges when the field has no name', () => {
		const valueChanges: Record<string, any> = {};
		applyAction({ type: 'select' }, { set_value: 'x' }, valueChanges);
		expect(valueChanges).toEqual({});
	});

	// GET /api/presets/{id}/form serializes an Action as a full pydantic
	// ActionSpec dump: a reaction whose YAML only declares `set_visibility:`
	// still arrives over the wire as `{ set_visibility: true, set_value: null,
	// set_disabled: null, update_options: null, update_validation: null,
	// set_filter_tags: null }`. `applyAction` must treat that explicit `null`
	// as "not specified", not as "clear the field" - otherwise a
	// visibility-only reaction resets the field's value/options/validation/
	// filter_tags to null every time it's reprocessed (see the end-to-end
	// case below).
	it('treats a server-serialized null action field as unset, not as a value to apply', () => {
		const valueChanges: Record<string, any> = {};
		const field: FieldConfig = {
			...baseField,
			value: 'euler',
			disabled: true,
			validation: { min: 0 },
			configuration: { filter_tags: ['kept'] }
		};
		const action: Action = {
			set_visibility: true,
			set_value: null as any,
			set_disabled: null as any,
			update_options: null as any,
			update_validation: null as any,
			set_filter_tags: null
		};
		const result = applyAction(field, action, valueChanges);
		expect(result.visible).toBe(true);
		expect(result.value).toBe('euler'); // untouched
		expect(result.disabled).toBe(true); // untouched
		expect(result.validation).toEqual({ min: 0 }); // untouched
		expect(result.configuration).toEqual({ filter_tags: ['kept'] }); // untouched
		expect(valueChanges).toEqual({}); // no spurious value change recorded
	});

	it('still applies explicit false/0/empty-string action values (not confused with null)', () => {
		const result = applyAction(baseField, { set_disabled: false, set_visibility: false });
		expect(result.disabled).toBe(false);
		expect(result.visible).toBe(false);
	});
});

describe('processFieldReactions / processAllFieldReactions', () => {
	it('applies matching reactions and skips non-matching ones', () => {
		const field: FieldConfig = {
			type: 'select',
			name: 'cfg',
			reactions: [
				{ when: { field: 'mode', equals: 'advanced' }, then: { set_visibility: true } },
				{ when: { field: 'mode', equals: 'basic' }, then: { set_visibility: false } }
			]
		};
		const result = processFieldReactions(field, { mode: 'advanced' });
		expect(result.visible).toBe(true);
	});

	it('leaves fields without reactions untouched', () => {
		const field: FieldConfig = { type: 'select', name: 'cfg' };
		expect(processFieldReactions(field, {})).toEqual(field);
	});

	it('swallows errors thrown while evaluating a reaction and continues', () => {
		const field: FieldConfig = {
			type: 'select',
			name: 'cfg',
			reactions: [
				// Malformed condition (no field/operator) still shouldn't throw out of the loop.
				{ when: {} as Condition, then: { set_visibility: true } },
				{ when: { field: 'mode', equals: 'advanced' }, then: { set_disabled: true } }
			]
		};
		const result = processFieldReactions(field, { mode: 'advanced' });
		expect(result.disabled).toBe(true);
	});

	it('recursively processes children fields', () => {
		const fields: FieldConfig[] = [
			{
				type: 'group',
				name: 'group1',
				children: [
					{
						type: 'select',
						name: 'child',
						reactions: [{ when: { field: 'trigger', equals: true }, then: { set_visibility: false } }]
					}
				]
			}
		];
		const result = processAllFieldReactions(fields, { trigger: true });
		expect(result[0].children?.[0].visible).toBe(false);
	});
});

describe('set_value loop-guard / dependency handling', () => {
	it('extracts field dependencies from a single condition', () => {
		const field: FieldConfig = {
			type: 'select',
			name: 'cfg',
			reactions: [{ when: { field: 'mode', equals: 'advanced' }, then: { set_visibility: true } }]
		};
		expect(extractFieldDependencies(field)).toEqual(['mode']);
	});

	it('extracts field dependencies from condition lists and logical groups, deduped', () => {
		const field: FieldConfig = {
			type: 'select',
			name: 'cfg',
			reactions: [
				{
					when: [
						{ field: 'a', equals: 1 },
						{ logic: 'OR', conditions: [{ field: 'b', equals: 2 }, { field: 'a', equals: 3 }] } as any
					],
					then: { set_visibility: true }
				}
			]
		};
		expect(new Set(extractFieldDependencies(field))).toEqual(new Set(['a', 'b']));
	});

	it('builds a dependency map only for fields that declare reactions', () => {
		const allFields: FieldConfig[] = [
			{ type: 'select', name: 'a' },
			{
				type: 'select',
				name: 'b',
				reactions: [{ when: { field: 'a', equals: 1 }, then: { set_visibility: true } }]
			}
		];
		expect(buildDependencyMap(allFields)).toEqual({ b: ['a'] });
	});

	it('resolves which fields should react to a changed field, preventing unrelated re-evaluation', () => {
		const dependencyMap = { b: ['a'], c: ['a', 'x'] };
		expect(getFieldsThatTriggerReactions('a', dependencyMap)).toEqual(['b', 'c']);
		expect(getFieldsThatTriggerReactions('x', dependencyMap)).toEqual(['c']);
		expect(getFieldsThatTriggerReactions('unrelated', dependencyMap)).toEqual([]);
	});

	it('extractAllFields walks nested schema children', () => {
		const schema = {
			properties: {
				root: {
					children: [
						{ type: 'select', name: 'a' },
						{ type: 'group', name: 'g', children: [{ type: 'select', name: 'b' }] }
					]
				}
			}
		};
		const fields = extractAllFields(schema);
		expect(fields.map((f) => f.name)).toEqual(['a', 'g', 'b']);
	});
});

describe('processSchemaWithReactions', () => {
	it('produces a processed schema and set_value changes without mutating the input', () => {
		const schema = {
			properties: {
				root: {
					children: [
						{
							type: 'select',
							name: 'target',
							reactions: [
								{ when: { field: 'trigger', equals: true }, then: { set_value: 'forced' } }
							]
						}
					]
				}
			}
		};
		const { processedSchema, valueChanges } = processSchemaWithReactions(schema, {
			trigger: true,
			target: 'original'
		});

		expect(valueChanges).toEqual({ target: 'forced' });
		expect(processedSchema.properties.root.children[0].value).toBe('forced');
		// Input schema is untouched (deep-copied before mutation).
		expect((schema as any).properties.root.children[0].value).toBeUndefined();
	});

	it('returns nulls/empty for a missing schema', () => {
		expect(processSchemaWithReactions(null, {})).toEqual({ processedSchema: null, valueChanges: {} });
	});

	// End-to-end regression for the live LTX-2 bug report: a "Face detection
	// model" field visible only while `enhance_faces_hands` is on (a
	// visibility-only reaction, no `set_value` in the preset YAML) appeared to
	// pick the model in the list but the selection never stuck. Root cause:
	// DynamicForm.svelte reprocesses the WHOLE schema on every formData change
	// (see its `$: if (formSchema && formData ...)` block), so the reprocess
	// triggered by the user's own click re-evaluated the still-true
	// `enhance_faces_hands` reaction and its server-serialized `set_value: null`
	// stomped the value right back to null before the picker could render it
	// as selected.
	it('does not wipe a value the user just set via a visibility-only reaction that still matches', () => {
		const schema = {
			properties: {
				root: {
					children: [
						{
							name: 'face_detector_model',
							type: 'model',
							reactions: [
								{
									when: { field: 'enhance_faces_hands', equals: false },
									then: {
										set_visibility: false,
										set_value: null,
										set_disabled: null,
										update_options: null,
										update_validation: null
									}
								},
								{
									when: { field: 'enhance_faces_hands', equals: true },
									then: {
										set_visibility: true,
										set_value: null,
										set_disabled: null,
										update_options: null,
										update_validation: null
									}
								}
							]
						}
					]
				}
			}
		};

		// Toggle is already on; user clicks a row in the picker.
		const formData = {
			enhance_faces_hands: true,
			face_detector_model: { modelPath: 'model:01KEVZXJAPH81SSR8RJ2P80XA0', tagFilters: [] }
		};

		const { valueChanges } = processSchemaWithReactions(schema, formData);

		// The still-matching visibility reaction must not report a value change -
		// there's nothing here for DynamicForm to merge back over the user's click.
		expect(valueChanges.face_detector_model).toBeUndefined();
	});
});
