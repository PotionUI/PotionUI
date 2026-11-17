import { describe, expect, it } from 'vitest';
import { buildFormSchema } from './formSchema';
import { getSchemaDefaults } from './defaults';

describe('buildFormSchema', () => {
	const fields = [
		{ type: 'slider', name: 'amount', title: 'Amount', default: 30 },
		{ type: 'boolean', name: 'soft', title: 'Soft' }
	];

	it('wraps a flat field list in one single-column root the form can render', () => {
		const schema = buildFormSchema(fields);

		const root = schema.properties.fields;
		expect(root.type).toBe('row');
		expect(root.configuration).toEqual({ columns: 1 });
		expect(root.children).toEqual(fields);
	});

	it('honours the column count and root name', () => {
		const schema = buildFormSchema(fields, { columns: 2, rootName: 'tool' });

		expect(Object.keys(schema.properties)).toEqual(['tool']);
		expect(schema.properties.tool.configuration).toEqual({ columns: 2 });
	});

	it('produces a schema whose defaults are the fields own defaults', () => {
		expect(getSchemaDefaults(buildFormSchema(fields))).toEqual({ amount: 30, soft: false });
	});
});
