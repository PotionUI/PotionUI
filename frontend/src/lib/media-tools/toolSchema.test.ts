import { describe, it, expect } from 'vitest';
import { paramField, toolParamsToSchema } from './toolSchema';
import type { ToolParam } from './types';

const option = (count: number) => Array.from({ length: count }, (_, index) => ({ label: `Option ${index}`, value: `v${index}` }));

describe('paramField', () => {
	it('draws a ranged number as a slider with bounds, step, unit, reset and no fill without an inner default', () => {
		const field = paramField({ name: 'tolerance', type: 'float', min: 0, max: 100, step: 5, unit: '%', label: 'Tolerance', default: 0 }) as unknown as Record<string, unknown>;
		expect(field).toMatchObject({ type: 'slider', name: 'tolerance', title: 'Tolerance', minimum: 0, maximum: 100, step: 5, unit: '%', reset: true, legend: false });
		expect(field.fill).toBeUndefined();
	});

	it('fills a slider from its default only when the default is strictly inside the range', () => {
		expect(paramField({ name: 'a', type: 'float', min: -1, max: 1, default: 0 })).toMatchObject({ fill: 'from-default' });
		expect((paramField({ name: 'a', type: 'float', min: 0, max: 1, default: 0 }) as unknown as Record<string, unknown>).fill).toBeUndefined();
		expect((paramField({ name: 'a', type: 'float', min: 0, max: 1 }) as unknown as Record<string, unknown>).fill).toBeUndefined();
	});

	it('lets a caller keep sliders as plain as the dialog draws them', () => {
		const field = paramField({ name: 'a', type: 'float', min: -1, max: 1, default: 0 }, { compactSliders: false }) as unknown as Record<string, unknown>;
		expect(field.reset).toBe(false);
		expect(field.fill).toBeUndefined();
		expect(field.legend).toBeUndefined();
	});

	it('steps an integer by one and a float by a hundredth when the tool says nothing', () => {
		expect(paramField({ name: 'a', type: 'integer', min: 0, max: 9 })).toMatchObject({ step: 1 });
		expect(paramField({ name: 'a', type: 'float', min: 0, max: 9 })).toMatchObject({ step: 0.01 });
	});

	it('draws a number without a usable range, or over a wide one, as a number box that shows Auto', () => {
		expect(paramField({ name: 'width', type: 'integer' })).toMatchObject({ type: 'number_box', integer: true, placeholder: 'Auto' });
		expect(paramField({ name: 'a', type: 'float', min: 0 })).toMatchObject({ type: 'number_box', integer: false });
		expect(paramField({ name: 'a', type: 'integer', min: 0, max: 50000 })).toMatchObject({ type: 'number_box' });
		expect(paramField({ name: 'a', type: 'float', min: 3, max: 3 })).toMatchObject({ type: 'number_box' });
	});

	it('draws a seed as a seed field with its own default and range', () => {
		expect(paramField({ name: 'seed', type: 'integer', min: 0, max: 2147483647 })).toMatchObject({ type: 'seed', minimum: 0, maximum: 2147483647, default: 0 });
		expect(paramField({ name: 'noise', type: 'integer', widget: 'seed' })).toMatchObject({ type: 'seed', minimum: 0, step: 1 });
	});

	it('draws four options or fewer as segmented and more as a select, with normalized options', () => {
		expect(paramField({ name: 'p', type: 'select', options: option(4) })).toMatchObject({ type: 'segmented' });
		expect(paramField({ name: 'p', type: 'select', options: option(5) })).toMatchObject({ type: 'select' });
		expect((paramField({ name: 'axis', type: 'select', options: ['horizontal', 'vertical'] }) as unknown as Record<string, unknown>).options).toEqual([
			{ label: 'Horizontal', value: 'horizontal' },
			{ label: 'Vertical', value: 'vertical' }
		]);
	});

	it('maps the remaining types to their own fields', () => {
		expect(paramField({ name: 'a', type: 'checkbox' })).toMatchObject({ type: 'checkbox' });
		expect(paramField({ name: 'a', type: 'color', allow_transparent: true })).toMatchObject({ type: 'color', allow_transparent: true });
		expect(paramField({ name: 'a', type: 'color' })).toMatchObject({ allow_transparent: false });
		expect(paramField({ name: 'a', type: 'anchor' })).toMatchObject({ type: 'anchor' });
		expect(paramField({ name: 'a', type: 'select', widget: 'anchor', options: option(9) })).toMatchObject({ type: 'anchor' });
		expect(paramField({ name: 'a', type: 'string', placeholder: 'x' })).toMatchObject({ type: 'string', placeholder: 'x' });
		expect(paramField({ name: 'a', type: 'lut', options: option(2) })).toMatchObject({ type: 'select' });
		expect(paramField({ name: 'a', type: 'look', options: option(2) })).toMatchObject({ type: 'select' });
	});

	it('carries label, description and default over and falls back to a humanized name', () => {
		expect(paramField({ name: 'keep_pitch', type: 'checkbox', description: 'Keeps the voice', default: true })).toMatchObject({ title: 'Keep pitch', description: 'Keeps the voice', default: true });
	});

	it('shows an unknown type as a text field and says so in development', () => {
		const warnings: unknown[][] = [];
		const original = console.warn;
		console.warn = (...args: unknown[]) => warnings.push(args);
		try {
			expect(paramField({ name: 'odd', type: 'plugin_thing' } as ToolParam)).toMatchObject({ type: 'string', name: 'odd' });
		} finally {
			console.warn = original;
		}
		expect(JSON.stringify(warnings)).toContain('plugin_thing');
	});
});

describe('toolParamsToSchema', () => {
	it('turns a when condition into a hidden field that a reaction reveals', () => {
		const [mode, color] = toolParamsToSchema([
			{ name: 'key_mode', type: 'select', options: ['auto', 'color'] },
			{ name: 'key_color', type: 'color', when: { field: 'key_mode', equals: 'color' } }
		]) as unknown as Array<Record<string, unknown>>;
		expect(mode.visible).toBeUndefined();
		expect(color.visible).toBe(false);
		expect(color.reactions).toEqual([{ when: { field: 'key_mode', equals: 'color' }, then: { set_visibility: true } }]);
	});

	it('puts neighbouring number boxes side by side in two columns', () => {
		const fields = toolParamsToSchema([
			{ name: 'width', type: 'integer' },
			{ name: 'height', type: 'integer' },
			{ name: 'depth', type: 'integer' },
			{ name: 'tone', type: 'float', min: 0, max: 1 }
		]) as unknown as Array<Record<string, unknown>>;
		expect(fields.map((field) => field.type)).toEqual(['row', 'number_box', 'slider']);
		expect(fields[0]).toMatchObject({ configuration: { columns: 2 } });
		expect((fields[0].children as Array<{ name: string }>).map((child) => child.name)).toEqual(['width', 'height']);
	});

	it('keeps number boxes flat when asked and never pairs a conditional one', () => {
		const flat = toolParamsToSchema([{ name: 'a', type: 'integer' }, { name: 'b', type: 'integer' }], { pairNumberBoxes: false });
		expect(flat.map((field) => field.type)).toEqual(['number_box', 'number_box']);
		const conditional = toolParamsToSchema([
			{ name: 'a', type: 'integer' },
			{ name: 'b', type: 'integer', when: { field: 'a', equals: 1 } }
		]);
		expect(conditional.map((field) => field.type)).toEqual(['number_box', 'number_box']);
	});

	it('keeps every param, in order, and returns nothing for none', () => {
		expect(toolParamsToSchema([])).toEqual([]);
		const names = toolParamsToSchema([
			{ name: 'a', type: 'float', min: 0, max: 1 },
			{ name: 'b', type: 'checkbox' },
			{ name: 'c', type: 'string' }
		]).map((field) => field.name);
		expect(names).toEqual(['a', 'b', 'c']);
	});
});
