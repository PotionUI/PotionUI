import { describe, expect, it } from 'vitest';
import type { CloudCapabilities } from '$lib/form/capabilityBinder';
import { buildAxisCandidates } from './candidates';
import { compareSchemaSignature, quantityFieldsOf, schemaForCompare } from './compareSchema';

const schema = {
	quantity_fields: ['count'],
	properties: {
		root: {
			type: 'tabs',
			children: [
				{
					type: 'tab',
					label: 'Generation',
					children: [
						{ name: 'model', type: 'model', configuration: { model_type: 'cloud', tasks: ['txt2img'] } },
						{ name: 'aspect_ratio', type: 'select', label: 'Aspect ratio', capability: { model_field: 'model', param: 'aspect_ratio' } },
						{ name: 'quality', type: 'select', label: 'Quality', capability: { model_field: 'model', param: 'quality' } },
						{ name: 'background', type: 'checkbox', label: 'Transparent background', capability: { model_field: 'model', param: 'background' } }
					]
				}
			]
		}
	}
};

const image: CloudCapabilities = {
	model_id: 'fake/image-1',
	params: [
		{ name: 'aspect_ratio', kind: 'enum', values: ['1:1', '16:9'] },
		{ name: 'quality', kind: 'range', minimum: 1, maximum: 10 }
	],
	inputs: []
};

const lookup = (id: string) => (id === 'fake/image-1' ? image : undefined);
const form = { model: 'model:fake/image-1' };

describe('schema for compare', () => {
	it('fills capability selects with the chosen model options so they become comparable', () => {
		const before = buildAxisCandidates(schema, form);
		expect(before.find((c) => c.field === 'aspect_ratio')?.unavailableReason).toBe('Not comparable');

		const after = buildAxisCandidates(schemaForCompare(schema, form, lookup), form);
		const aspect = after.find((c) => c.field === 'aspect_ratio')!;
		const quality = after.find((c) => c.field === 'quality')!;
		expect(aspect.unavailableReason).toBeNull();
		expect(aspect.options.map((o) => o.value)).toEqual(['1:1', '16:9']);
		expect(quality.unavailableReason).toBeNull();
		expect(quality.options.map((o) => o.value)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
		expect(after.find((c) => c.field === 'background')?.unavailableReason).not.toBeNull();
	});

	it('leaves the source schema untouched and keeps it as is before the capabilities load', () => {
		const snapshot = JSON.stringify(schema);
		schemaForCompare(schema, form, lookup);
		expect(JSON.stringify(schema)).toBe(snapshot);
		const unloaded = buildAxisCandidates(schemaForCompare(schema, form, () => undefined), form);
		expect(unloaded.find((c) => c.field === 'aspect_ratio')?.unavailableReason).toBe('Not comparable');
	});

	it('changes signature when the model or the capability revision changes, not on other edits', () => {
		const base = compareSchemaSignature('k', schema, form, 1);
		expect(compareSchemaSignature('k', schema, { ...form, prompt: 'x' }, 1)).toBe(base);
		expect(compareSchemaSignature('k', schema, { model: 'model:fake/lite-1' }, 1)).not.toBe(base);
		expect(compareSchemaSignature('k', schema, form, 2)).not.toBe(base);
	});

	it('reads the quantity fields the server declared', () => {
		expect(quantityFieldsOf(schema)).toEqual(['count']);
		expect(quantityFieldsOf({ properties: {} })).toEqual([]);
		expect(quantityFieldsOf(null)).toEqual([]);
	});
});
