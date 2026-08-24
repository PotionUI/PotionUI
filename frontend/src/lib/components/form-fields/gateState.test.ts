import { describe, expect, it } from 'vitest';
import { gateHasChildren, gateRegionId, resolveGateOn } from './gateState';

describe('resolveGateOn', () => {
	it('reads its own value out of the ambient value object by name', () => {
		expect(resolveGateOn({ name: 'enhance' }, { enhance: true })).toBe(true);
		expect(resolveGateOn({ name: 'enhance' }, { enhance: false })).toBe(false);
	});

	it('coerces truthy/falsy non-boolean stored values', () => {
		expect(resolveGateOn({ name: 'enhance' }, { enhance: 1 })).toBe(true);
		expect(resolveGateOn({ name: 'enhance' }, { enhance: 0 })).toBe(false);
	});

	it('falls back to config.default when the key is absent', () => {
		expect(resolveGateOn({ name: 'enhance', default: true }, {})).toBe(true);
		expect(resolveGateOn({ name: 'enhance', default: false }, {})).toBe(false);
		expect(resolveGateOn({ name: 'enhance' }, {})).toBe(false);
	});

	it('falls back to config.default when the stored value is null/undefined', () => {
		expect(resolveGateOn({ name: 'enhance', default: true }, { enhance: null })).toBe(true);
		expect(resolveGateOn({ name: 'enhance', default: true }, { enhance: undefined })).toBe(true);
	});

	it.each([
		['no ambient value object', { name: 'enhance' }, undefined],
		['non-object ambient value', { name: 'enhance' }, 'not-an-object'],
		['missing name', {}, { enhance: true }]
	])('is false for %s', (_label, config, value) => {
		expect(resolveGateOn(config, value)).toBe(false);
	});
});

describe('gateHasChildren', () => {
	it('is true only for a non-empty children array', () => {
		expect(gateHasChildren({ children: [{ type: 'slider' }] })).toBe(true);
	});

	it.each([
		['absent', {}],
		['empty array', { children: [] }],
		['null config', null],
		['undefined config', undefined],
		['non-array children', { children: 'nope' }]
	])('is false for %s', (_label, config) => {
		expect(gateHasChildren(config)).toBe(false);
	});
});

describe('gateRegionId', () => {
	it('derives a stable id from the field name', () => {
		expect(gateRegionId({ name: 'enhance' })).toBe('gate-enhance-content');
	});

	it('falls back to a generic id when name is missing', () => {
		expect(gateRegionId({})).toBe('gate-field-content');
		expect(gateRegionId(null)).toBe('gate-field-content');
	});
});
