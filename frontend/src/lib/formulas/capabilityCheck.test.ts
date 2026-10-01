import { describe, expect, it } from 'vitest';
import { buildCapabilityCheck } from './capabilityCheck';
import type { CloudCapabilities } from '$lib/form/capabilityBinder';

const MODEL = { type: 'model', name: 'model', configuration: { model_type: 'cloud' } };
const GUIDANCE = { type: 'slider', name: 'guidance', capability: { model_field: 'model', param: 'guidance' } };
const ASPECT = { type: 'select', name: 'aspect', capability: { model_field: 'model', param: 'aspect' }, options: [{ label: 'a', value: '1:1' }] };
const PLAIN = { type: 'slider', name: 'steps' };

function schema(fields: unknown[]) {
	return { properties: { root: { children: fields } } };
}

function caps(id: string, extra: Partial<CloudCapabilities> = {}): CloudCapabilities {
	return {
		model_id: id,
		params: [{ name: 'guidance', kind: 'range', minimum: 1, maximum: 10 }],
		inputs: [],
		...extra
	};
}

const catalog: Record<string, CloudCapabilities> = {
	small: caps('small'),
	large: caps('large', { params: [{ name: 'guidance', kind: 'range', minimum: 1, maximum: 100 }] })
};
const lookup = (id: string) => catalog[id];

describe('buildCapabilityCheck', () => {
	it('returns nothing when the form has no capability-bound model', () => {
		expect(buildCapabilityCheck(schema([PLAIN]), {})).toBeUndefined();
	});

	it('reports a value outside the current model range', () => {
		const check = buildCapabilityCheck(schema([MODEL, GUIDANCE]), { model: 'model:small' }, lookup)!;
		expect(check({ guidance: 50 })).toEqual({ guidance: [] });
		expect(check({ guidance: 5 })).toEqual({});
	});

	it('maps a field the model hides to a whole-field rejection', () => {
		const check = buildCapabilityCheck(schema([MODEL, GUIDANCE, ASPECT]), { model: 'model:small' }, lookup)!;
		expect(check({ aspect: '1:1' })).toEqual({ aspect: [] });
	});

	it('validates against the model the patch switches to', () => {
		const check = buildCapabilityCheck(schema([MODEL, GUIDANCE]), { model: 'model:small' }, lookup)!;
		expect(check({ model: 'model:large', guidance: 50 })).toEqual({});
		const back = buildCapabilityCheck(schema([MODEL, GUIDANCE]), { model: 'model:large' }, lookup)!;
		expect(back({ model: 'model:small', guidance: 50 })).toEqual({ guidance: [] });
	});

	it('checks nothing while the new model capabilities are not loaded', () => {
		const check = buildCapabilityCheck(schema([MODEL, GUIDANCE]), { model: 'model:small' }, lookup)!;
		expect(check({ model: 'model:unknown', guidance: 50 })).toEqual({});
	});
});
