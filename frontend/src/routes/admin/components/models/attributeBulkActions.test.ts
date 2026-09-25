import { describe, it, expect } from 'vitest';
import type { AttributeDefinition } from '$lib/types/models';
import { deletableAttributeIds, selectionHasBuiltIn } from './attributeBulkActions';

function definition(overrides: Partial<AttributeDefinition> = {}): AttributeDefinition {
	return {
		id: 'd1',
		key: 'strength',
		label: 'Strength',
		field_type: 'slider',
		model_types: [],
		config: {},
		default_value: null,
		per_user: false,
		admin_only: false,
		system: false,
		source: 'custom',
		...overrides
	};
}

describe('deletableAttributeIds', () => {
	const custom = definition({ id: 'custom-1', system: false });
	const builtIn = definition({ id: 'builtin-1', system: true });
	const all = [custom, builtIn];

	it('keeps only custom ids from the selection', () => {
		expect(deletableAttributeIds(all, new Set(['custom-1', 'builtin-1']))).toEqual(['custom-1']);
	});

	it('is empty when only built-ins are selected', () => {
		expect(deletableAttributeIds(all, new Set(['builtin-1']))).toEqual([]);
	});

	it('is empty for an empty selection', () => {
		expect(deletableAttributeIds(all, new Set())).toEqual([]);
	});
});

describe('selectionHasBuiltIn', () => {
	const custom = definition({ id: 'custom-1', system: false });
	const builtIn = definition({ id: 'builtin-1', system: true });
	const all = [custom, builtIn];

	it('is true when the selection includes a built-in', () => {
		expect(selectionHasBuiltIn(all, new Set(['custom-1', 'builtin-1']))).toBe(true);
	});

	it('is false when the selection is only custom rows', () => {
		expect(selectionHasBuiltIn(all, new Set(['custom-1']))).toBe(false);
	});
});
