import { describe, it, expect } from 'vitest';
import { applyFieldReadonly, applyReadonlyToSchema } from './readonlyFilter';

interface ReadonlyTestNode {
	name?: string;
	type?: string;
	readonly?: boolean;
	disabled?: boolean;
	children?: ReadonlyTestNode[];
	[key: string]: unknown;
}

describe('applyFieldReadonly', () => {
	it('sets disabled on a readonly leaf field', () => {
		const field: ReadonlyTestNode = { name: 'steps', readonly: true };
		applyFieldReadonly(field);
		expect(field.disabled).toBe(true);
	});

	it('leaves a non-readonly field untouched', () => {
		const field: ReadonlyTestNode = { name: 'steps', disabled: false };
		applyFieldReadonly(field);
		expect(field.disabled).toBe(false);
	});

	it('never clears a disabled a reaction already set', () => {
		const field: ReadonlyTestNode = { name: 'steps', readonly: false, disabled: true };
		applyFieldReadonly(field);
		expect(field.disabled).toBe(true);
	});

	it('recurses into nested children (tabs > tab > field)', () => {
		const tabs: ReadonlyTestNode = {
			type: 'tabs',
			children: [
				{
					type: 'tab',
					children: [{ name: 'cfg_scale', readonly: true }]
				}
			]
		};
		applyFieldReadonly(tabs);
		expect(tabs.children?.[0].children?.[0].disabled).toBe(true);
	});
});

describe('applyReadonlyToSchema', () => {
	it('applies readonly across every root property', () => {
		const schema: { properties: Record<string, { children: ReadonlyTestNode[] }> } = {
			properties: {
				root: {
					children: [
						{ name: 'a', readonly: true },
						{ name: 'b' }
					]
				}
			}
		};
		applyReadonlyToSchema(schema);
		expect(schema.properties.root.children[0].disabled).toBe(true);
		expect(schema.properties.root.children[1].disabled).toBeUndefined();
	});

	it('is a no-op on a schema with no properties', () => {
		expect(applyReadonlyToSchema(null)).toBeNull();
		expect(applyReadonlyToSchema(undefined)).toBeUndefined();
		expect(applyReadonlyToSchema({})).toEqual({});
	});
});
