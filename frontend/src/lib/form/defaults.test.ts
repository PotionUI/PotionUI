import { describe, it, expect } from 'vitest';
import { getSchemaDefaults } from './defaults';

const NATIVE_LIKE = {
	properties: {
		tabs: {
			type: 'tabs',
			children: [
				{
					type: 'tab',
					title: 'Generation',
					children: [
						{
							type: 'row',
							children: [
								{ type: 'model', name: 'model', default: 'model:abc', configuration: { tags: ['sdxl'] } },
								{ type: 'seed', name: 'seed', default: -1 }
							]
						},
						{ type: 'select', name: 'sampler', default: 'euler' },
						{ type: 'select', name: 'scheduler' },
						{ type: 'checkbox', name: 'hires', default: true },
						{ type: 'checkbox', name: 'tiled' },
						{ type: 'checkbox_group', name: 'extras' },
						{ type: 'lora_picker', name: 'loras' },
						{ type: 'slider', name: 'steps', default: 30 },
						{ type: 'string', name: 'note' },
						{ type: 'alert', title: 'Heads up' },
						{
							type: 'accordion',
							children: [{ type: 'number', name: 'cfg', default: 7 }]
						}
					]
				}
			]
		}
	}
};

describe('getSchemaDefaults', () => {
	it('seeds a single-root native-style form exactly as before', () => {
		expect(getSchemaDefaults(NATIVE_LIKE)).toEqual({
			model: { modelPath: 'model:abc', tagFilters: ['sdxl'] },
			seed: -1,
			sampler: 'euler',
			scheduler: undefined,
			hires: true,
			tiled: false,
			extras: [],
			loras: [],
			steps: 30,
			cfg: 7
		});
	});

	it('seeds defaults from every root of a multi-root form', () => {
		const schema = {
			properties: {
				model_row: { type: 'row', children: [{ type: 'model', name: 'model', default: 'model:a' }] },
				tabs: { type: 'tabs', children: [{ type: 'tab', children: [{ type: 'stepper', name: 'count', default: 1 }] }] }
			}
		};
		expect(getSchemaDefaults(schema)).toEqual({
			model: { modelPath: 'model:a', tagFilters: [] },
			count: 1
		});
	});

	it('lets the earlier root win when two roots define the same field', () => {
		const schema = {
			properties: {
				first: { type: 'row', children: [{ type: 'slider', name: 'steps', default: 10 }] },
				second: { type: 'row', children: [{ type: 'slider', name: 'steps', default: 99 }, { type: 'slider', name: 'cfg', default: 4 }] }
			}
		};
		expect(getSchemaDefaults(schema)).toEqual({ steps: 10, cfg: 4 });
	});

	it('is empty without a schema, properties or roots', () => {
		expect(getSchemaDefaults(null)).toEqual({});
		expect(getSchemaDefaults({})).toEqual({});
		expect(getSchemaDefaults({ properties: {} })).toEqual({});
	});
});
