import { describe, it, expect } from 'vitest';
import {
	applyCapabilitiesToSchema,
	capabilityValueChanges,
	cloudModelId,
	collectCapabilityModelFields,
	entryApplies,
	humanizeParamName,
	modeTasksFor,
	omitCapabilityHidden,
	resolveCapabilities,
	resolveCloudOptionParams,
	splitOptionErrors,
	type CapabilityParam,
	type CloudCapabilities
} from './capabilityBinder';

function caps(overrides: Partial<CloudCapabilities> = {}): CloudCapabilities {
	return {
		model_id: 'm1',
		params: [
			{ name: 'aspect_ratio', kind: 'enum', values: ['1:1', '16:9'], default: '1:1' },
			{ name: 'guidance', kind: 'range', minimum: 1, maximum: 10, step: 0.5, default: 5 },
			{ name: 'x.style', kind: 'enum', values: ['noir', 'pop'], extra: true, label: 'Style' },
			{ name: 'x.hdr', kind: 'boolean', extra: true },
			{ name: 'negative_prompt', kind: 'text' }
		],
		inputs: [{ role: 'reference', modality: 'image', max_items: 3 }],
		...overrides
	};
}

function schemaWith(fields: any[]) {
	return JSON.parse(JSON.stringify({ properties: { root: { children: fields } } }));
}

const MODEL = { type: 'model', name: 'model', configuration: { model_type: 'cloud', tasks: ['txt2img'] } };
const ASPECT = {
	type: 'select',
	name: 'aspect_ratio',
	options: [
		{ label: 'Square', value: '1:1' },
		{ label: 'Wide', value: '16:9' },
		{ label: 'Ultra', value: '21:9' }
	],
	capability: { model_field: 'model', param: 'aspect_ratio' }
};
const GUIDANCE = { type: 'slider', name: 'guidance', minimum: 0, maximum: 100, capability: { model_field: 'model', param: 'guidance' } };
const REFS = { type: 'image', name: 'references', capability: { model_field: 'model', input: 'reference' } };
const FIRST = { type: 'image', name: 'first_frame', capability: { model_field: 'model', input: 'first_frame' } };
const OPTIONS = { type: 'cloud_options', name: 'provider_options', capability: { model_field: 'model' }, configuration: { include_unbound: true } };

function fieldOf(schema: any, name: string) {
	return schema.properties.root.children.find((field: any) => field.name === name);
}

describe('cloudModelId', () => {
	it('reads the id from a model reference', () => {
		expect(cloudModelId({ modelPath: 'model:abc', tagFilters: [] })).toBe('abc');
		expect(cloudModelId('model:xyz')).toBe('xyz');
	});

	it('is null for anything that is not a model reference', () => {
		expect(cloudModelId({ modelPath: '' })).toBeNull();
		expect(cloudModelId('checkpoints/a.safetensors')).toBeNull();
		expect(cloudModelId(undefined)).toBeNull();
		expect(cloudModelId('model:')).toBeNull();
	});
});

describe('collecting bindings', () => {
	it('finds each referenced model field once', () => {
		expect(collectCapabilityModelFields(schemaWith([MODEL, ASPECT, GUIDANCE, REFS]))).toEqual(['model']);
		expect(collectCapabilityModelFields(schemaWith([MODEL]))).toEqual([]);
	});

	it('finds bindings nested in containers', () => {
		const nested = schemaWith([{ type: 'row', children: [ASPECT] }]);
		expect(collectCapabilityModelFields(nested)).toEqual(['model']);
	});

	it('reads the mode tasks from the model field', () => {
		expect(modeTasksFor(schemaWith([MODEL]), 'model')).toEqual(['txt2img']);
		expect(modeTasksFor(schemaWith([{ type: 'model', name: 'model' }]), 'model')).toEqual([]);
	});
});

describe('entryApplies', () => {
	it('applies when the entry or the mode names no task', () => {
		expect(entryApplies([], ['txt2img'])).toBe(true);
		expect(entryApplies(undefined, ['txt2img'])).toBe(true);
		expect(entryApplies(['txt2img'], [])).toBe(true);
	});

	it('applies when the tasks intersect', () => {
		expect(entryApplies(['txt2video', 'img2video'], ['img2video'])).toBe(true);
		expect(entryApplies(['txt2video'], ['txt2img'])).toBe(false);
		expect(entryApplies(['txt2video'], ['txt2img', 'txt2video'])).toBe(true);
	});
});

describe('applyCapabilitiesToSchema', () => {
	it('replaces the options of an enum with the ones the model offers, keeping known labels', () => {
		const schema = schemaWith([MODEL, ASPECT]);
		const hidden = applyCapabilitiesToSchema(schema, { model: caps() });
		expect(hidden).toEqual([]);
		expect(fieldOf(schema, 'aspect_ratio').options).toEqual([
			{ label: 'Square', value: '1:1' },
			{ label: 'Wide', value: '16:9' }
		]);
	});

	it('hides a field whose param the model lacks', () => {
		const schema = schemaWith([MODEL, ASPECT]);
		const hidden = applyCapabilitiesToSchema(schema, { model: caps({ params: [] }) });
		expect(hidden).toEqual(['aspect_ratio']);
		expect(fieldOf(schema, 'aspect_ratio').visible).toBe(false);
	});

	it('hides a field whose param is for another task', () => {
		const schema = schemaWith([MODEL, ASPECT]);
		const other = caps({ params: [{ name: 'aspect_ratio', kind: 'enum', values: ['1:1'], tasks: ['txt2video'] }] });
		expect(applyCapabilitiesToSchema(schema, { model: other })).toEqual(['aspect_ratio']);
	});

	it('constrains a range', () => {
		const schema = schemaWith([MODEL, GUIDANCE]);
		applyCapabilitiesToSchema(schema, { model: caps() });
		const field = fieldOf(schema, 'guidance');
		expect([field.minimum, field.maximum, field.step]).toEqual([1, 10, 0.5]);
	});

	it('turns a range bound to a select into one option per step', () => {
		const select = { type: 'select', name: 'quality', capability: { model_field: 'model', param: 'quality' } };
		const schema = schemaWith([MODEL, select]);
		const quality = caps({ params: [{ name: 'quality', kind: 'range', minimum: 1, maximum: 4, integer: true }] });
		applyCapabilitiesToSchema(schema, { model: quality });
		expect(fieldOf(schema, 'quality').options).toEqual([
			{ label: '1', value: 1 },
			{ label: '2', value: 2 },
			{ label: '3', value: 3 },
			{ label: '4', value: 4 }
		]);
	});

	it('steps a fractional range bound to a select', () => {
		const select = { type: 'select', name: 'quality', capability: { model_field: 'model', param: 'quality' } };
		const schema = schemaWith([MODEL, select]);
		const half = caps({ params: [{ name: 'quality', kind: 'range', minimum: 0, maximum: 1, step: 0.25 }] });
		applyCapabilitiesToSchema(schema, { model: half });
		expect(fieldOf(schema, 'quality').options.map((o: { value: number }) => o.value)).toEqual([0, 0.25, 0.5, 0.75, 1]);
	});

	it('lists at most fifty steps exactly and samples wider ranges', () => {
		const select = { type: 'select', name: 'quality', capability: { model_field: 'model', param: 'quality' } };
		const fifty = caps({ params: [{ name: 'quality', kind: 'range', minimum: 1, maximum: 50, integer: true }] });
		const exact = schemaWith([MODEL, select]);
		applyCapabilitiesToSchema(exact, { model: fifty });
		expect(fieldOf(exact, 'quality').options).toHaveLength(50);

		const wide = caps({ params: [{ name: 'quality', kind: 'range', minimum: 0, maximum: 1000, integer: true, default: 333 }] });
		const sampled = schemaWith([MODEL, select]);
		applyCapabilitiesToSchema(sampled, { model: wide });
		const values = fieldOf(sampled, 'quality').options.map((option: { value: number }) => option.value);
		expect(values.length).toBeLessThanOrEqual(12);
		expect(values[0]).toBe(0);
		expect(values[values.length - 1]).toBe(1000);
		expect(values).toContain(333);
		expect(values).toEqual([...values].sort((a: number, b: number) => a - b));
		expect(new Set(values).size).toBe(values.length);
	});

	it('keeps every sampled value on the step grid', () => {
		const select = { type: 'select', name: 'quality', capability: { model_field: 'model', param: 'quality' } };
		const schema = schemaWith([MODEL, select]);
		const grid = caps({ params: [{ name: 'quality', kind: 'range', minimum: 10, maximum: 1010, step: 10, default: 505 }] });
		applyCapabilitiesToSchema(schema, { model: grid });
		const values = fieldOf(schema, 'quality').options.map((option: { value: number }) => option.value);
		for (const value of values) expect((value - 10) % 10).toBe(0);
		expect(values[0]).toBe(10);
		expect(values[values.length - 1]).toBe(1010);
	});

	it('leaves a select alone when the range has no bounds', () => {
		const select = { type: 'select', name: 'quality', options: [{ label: 'x', value: 1 }], capability: { model_field: 'model', param: 'quality' } };
		const schema = schemaWith([MODEL, select]);
		const open = caps({ params: [{ name: 'quality', kind: 'range' }] });
		applyCapabilitiesToSchema(schema, { model: open });
		expect(fieldOf(schema, 'quality').options).toEqual([{ label: 'x', value: 1 }]);
	});

	it('makes an integer range step in whole numbers', () => {
		const schema = schemaWith([MODEL, GUIDANCE]);
		const integer = caps({ params: [{ name: 'guidance', kind: 'range', minimum: 1, maximum: 4, step: 0.25, integer: true }] });
		applyCapabilitiesToSchema(schema, { model: integer });
		expect(fieldOf(schema, 'guidance').step).toBe(1);
		expect(fieldOf(schema, 'guidance').integer).toBe(true);
	});

	it('hides a media field for a role the model does not accept and keeps one it does', () => {
		const schema = schemaWith([MODEL, REFS, FIRST]);
		expect(applyCapabilitiesToSchema(schema, { model: caps() })).toEqual(['first_frame']);
		expect(fieldOf(schema, 'references').visible).toBeUndefined();
	});

	it('leaves the fields as declared while the capabilities are unknown', () => {
		const schema = schemaWith([MODEL, ASPECT, REFS]);
		expect(applyCapabilitiesToSchema(schema, { model: null })).toEqual([]);
		expect(applyCapabilitiesToSchema(schema, {})).toEqual([]);
		expect(fieldOf(schema, 'aspect_ratio').options).toHaveLength(3);
		expect(fieldOf(schema, 'aspect_ratio').visible).toBeUndefined();
	});

	it('gives the options field its params, and hides it when there are none', () => {
		const schema = schemaWith([MODEL, ASPECT, OPTIONS]);
		applyCapabilitiesToSchema(schema, { model: caps() });
		const names = fieldOf(schema, 'provider_options').resolved_params.map((param: CapabilityParam) => param.name);
		expect(names).toEqual(['guidance', 'x.style', 'x.hdr', 'negative_prompt']);

		const empty = schemaWith([MODEL, OPTIONS]);
		const hidden = applyCapabilitiesToSchema(empty, { model: caps({ params: [] }) });
		expect(hidden).toEqual(['provider_options']);
		expect(fieldOf(empty, 'provider_options').visible).toBe(false);
	});

	it('hides the options field and keeps it out of the submission while the capabilities are unknown', () => {
		const schema = schemaWith([MODEL, OPTIONS]);
		expect(applyCapabilitiesToSchema(schema, { model: null })).toEqual(['provider_options']);
		expect(fieldOf(schema, 'provider_options').visible).toBe(false);
	});
});

describe('resolveCloudOptionParams', () => {
	it('lists extras only unless unbound params are requested', () => {
		expect(resolveCloudOptionParams(caps(), [], new Set(), false).map((p) => p.name)).toEqual(['x.style', 'x.hdr']);
	});

	it('leaves out canonical params another field claims', () => {
		const names = resolveCloudOptionParams(caps(), [], new Set(['aspect_ratio', 'guidance']), true).map((p) => p.name);
		expect(names).toEqual(['x.style', 'x.hdr', 'negative_prompt']);
	});

	it('filters by task and handles missing capabilities', () => {
		const scoped = caps({ params: [{ name: 'x.len', kind: 'text', extra: true, tasks: ['txt2video'] }] });
		expect(resolveCloudOptionParams(scoped, ['txt2img'], new Set(), true)).toEqual([]);
		expect(resolveCloudOptionParams(null, [], new Set(), true)).toEqual([]);
	});
});

describe('capabilityValueChanges', () => {
	function applied(fields: any[], c: CloudCapabilities) {
		const schema = schemaWith(fields);
		applyCapabilitiesToSchema(schema, { model: c });
		return schema;
	}

	it('resets an enum value the model no longer offers to the default', () => {
		const schema = applied([MODEL, ASPECT], caps());
		expect(capabilityValueChanges(schema, { aspect_ratio: '21:9' }, { model: caps() })).toEqual({ aspect_ratio: '1:1' });
	});

	it('falls back to the first option without a default', () => {
		const noDefault = caps({ params: [{ name: 'aspect_ratio', kind: 'enum', values: ['4:3', '1:1'] }] });
		const schema = applied([MODEL, ASPECT], noDefault);
		expect(capabilityValueChanges(schema, { aspect_ratio: '21:9' }, { model: noDefault })).toEqual({ aspect_ratio: '4:3' });
	});

	it('keeps a valid value and ignores an empty optional one', () => {
		const schema = applied([MODEL, ASPECT], caps());
		expect(capabilityValueChanges(schema, { aspect_ratio: '16:9' }, { model: caps() })).toEqual({});
		expect(capabilityValueChanges(schema, {}, { model: caps() })).toEqual({});
	});

	it('fills a required enum that is empty', () => {
		const required = caps({ params: [{ name: 'aspect_ratio', kind: 'enum', values: ['1:1', '16:9'], required: true, default: '16:9' }] });
		const schema = applied([MODEL, ASPECT], required);
		expect(capabilityValueChanges(schema, { aspect_ratio: null }, { model: required })).toEqual({ aspect_ratio: '16:9' });
	});

	it('pulls an out-of-range number back into range', () => {
		const schema = applied([MODEL, GUIDANCE], caps());
		expect(capabilityValueChanges(schema, { guidance: 50 }, { model: caps() })).toEqual({ guidance: 5 });
		const noDefault = caps({ params: [{ name: 'guidance', kind: 'range', minimum: 1, maximum: 10 }] });
		expect(capabilityValueChanges(schema, { guidance: 50 }, { model: noDefault })).toEqual({ guidance: 10 });
	});

	it('rejects a fraction for an integer range', () => {
		const integer = caps({ params: [{ name: 'guidance', kind: 'range', minimum: 1, maximum: 10, integer: true }] });
		const schema = applied([MODEL, GUIDANCE], integer);
		expect(capabilityValueChanges(schema, { guidance: 2.5 }, { model: integer })).toEqual({ guidance: 3 });
	});

	it('does not touch a hidden field', () => {
		const schema = applied([MODEL, ASPECT], caps({ params: [] }));
		expect(capabilityValueChanges(schema, { aspect_ratio: '21:9' }, { model: caps({ params: [] }) })).toEqual({});
	});

	it('does nothing while the capabilities are unknown', () => {
		const schema = schemaWith([MODEL, ASPECT]);
		expect(capabilityValueChanges(schema, { aspect_ratio: '21:9' }, { model: null })).toEqual({});
	});

	it('drops option keys the model no longer offers and repairs invalid ones', () => {
		const schema = applied([MODEL, OPTIONS], caps());
		const current = { provider_options: { 'x.style': 'retro', 'x.hdr': true, 'x.gone': 'a' } };
		expect(capabilityValueChanges(schema, current, { model: caps() })).toEqual({
			provider_options: { 'x.style': 'noir', 'x.hdr': true }
		});
	});
});

describe('omitCapabilityHidden', () => {
	it('drops a hidden field and its siblings but nothing else', () => {
		const data = { first_frame: 'a.png', first_frame__origin: 'x', first_frame_inpaint_mask: 'm', first_frame_other: 1, seed: 1 };
		expect(omitCapabilityHidden(data, new Set(['first_frame']))).toEqual({ first_frame_other: 1, seed: 1 });
	});

	it('returns the data untouched when nothing is hidden', () => {
		const data = { a: 1 };
		expect(omitCapabilityHidden(data, new Set())).toBe(data);
	});
});

describe('splitOptionErrors', () => {
	it('assigns prefixed messages to their key and keeps the rest', () => {
		const result = splitOptionErrors(["x.style: 'retro' is not offered", 'something else'], ['x.style', 'x.hdr']);
		expect(result.byKey).toEqual({ 'x.style': ["'retro' is not offered"] });
		expect(result.rest).toEqual(['something else']);
	});
});

describe('humanizeParamName', () => {
	it('drops the extras prefix and spaces the words', () => {
		expect(humanizeParamName('x.color_grade')).toBe('Color grade');
		expect(humanizeParamName('aspect_ratio')).toBe('Aspect ratio');
	});
});

describe('resolveCapabilities', () => {
	const a = caps({ model_id: 'a' });
	const b = caps({ model_id: 'b' });
	const lookup = (id: string) => ({ a, b })[id as 'a' | 'b'];

	it('picks the capabilities of the model currently chosen in each field', () => {
		expect(resolveCapabilities(['model'], { model: { modelPath: 'model:a' } }, lookup)).toEqual({ model: a });
		expect(resolveCapabilities(['model'], { model: { modelPath: 'model:b' } }, lookup)).toEqual({ model: b });
	});

	it('is null for a model that is not loaded, not a cloud model or not chosen', () => {
		expect(resolveCapabilities(['model'], { model: { modelPath: 'model:zzz' } }, lookup)).toEqual({ model: null });
		expect(resolveCapabilities(['model'], { model: { modelPath: 'x/y.safetensors' } }, lookup)).toEqual({ model: null });
		expect(resolveCapabilities(['model'], {}, lookup)).toEqual({ model: null });
	});
});
