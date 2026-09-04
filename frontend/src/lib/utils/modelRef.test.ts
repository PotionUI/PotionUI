import { describe, expect, it } from 'vitest';
import {
	MODEL_REF_PREFIX,
	refFor,
	matchesStoredValue,
	findModelForValue,
	legacyValuesNeedingLookup
} from './modelRef';

describe('refFor', () => {
	it('prefers a model:<id> ref when the model has an id', () => {
		expect(refFor({ id: 'abc', file_path: '/models/x.safetensors' })).toBe('model:abc');
	});

	it('falls back to file_path when there is no id', () => {
		expect(refFor({ file_path: '/models/x.safetensors' })).toBe('/models/x.safetensors');
	});

	it('is empty for a model with neither', () => {
		expect(refFor({})).toBe('');
		expect(refFor(null)).toBe('');
		expect(refFor(undefined)).toBe('');
	});
});

describe('matchesStoredValue', () => {
	it('matches a model:<id> ref by id', () => {
		expect(matchesStoredValue({ id: 'abc' }, `${MODEL_REF_PREFIX}abc`)).toBe(true);
		expect(matchesStoredValue({ id: 'other' }, `${MODEL_REF_PREFIX}abc`)).toBe(false);
	});

	it('matches a legacy value by exact file_path', () => {
		expect(matchesStoredValue({ file_path: '/models/x.safetensors' }, '/models/x.safetensors')).toBe(true);
	});

	it('matches a legacy value by bare filename', () => {
		expect(matchesStoredValue({ filename: 'x.safetensors' }, '/some/other/dir/x.safetensors')).toBe(true);
	});

	it('is false when nothing matches', () => {
		expect(matchesStoredValue({ id: 'abc', filename: 'x.safetensors' }, 'y.safetensors')).toBe(false);
	});

	it.each([
		['no model', null, 'model:abc'],
		['no stored value', { id: 'abc' }, null],
		['empty stored value', { id: 'abc' }, '']
	])('is false for %s', (_label, model, storedValue) => {
		expect(matchesStoredValue(model as any, storedValue as any)).toBe(false);
	});
});

describe('findModelForValue', () => {
	it('returns the first matching candidate', () => {
		const list = [{ id: '1' }, { id: '2' }, { id: '3' }];
		expect(findModelForValue(`${MODEL_REF_PREFIX}2`, list)).toBe(list[1]);
	});

	it('returns undefined when nothing matches', () => {
		expect(findModelForValue(`${MODEL_REF_PREFIX}missing`, [{ id: '1' }])).toBeUndefined();
	});
});

describe('legacyValuesNeedingLookup', () => {
	it('skips model:<id> refs - those are hydrated by id, not by filename', () => {
		expect(legacyValuesNeedingLookup([`${MODEL_REF_PREFIX}abc`], [], new Set())).toEqual([]);
	});

	it('returns a legacy value not yet resolved in models', () => {
		const path = 'models/loras/krea2/detail_slider_krea2_loraholic.safetensors';
		expect(legacyValuesNeedingLookup([path], [], new Set())).toEqual([path]);
	});

	it('excludes a legacy value already resolved against models', () => {
		const path = 'models/loras/krea2/x.safetensors';
		const models = [{ filename: 'x.safetensors' }];
		expect(legacyValuesNeedingLookup([path], models, new Set())).toEqual([]);
	});

	it('excludes a value already attempted', () => {
		const path = 'models/loras/krea2/x.safetensors';
		expect(legacyValuesNeedingLookup([path], [], new Set([path]))).toEqual([]);
	});

	it('drops nullish/empty entries and dedupes repeats', () => {
		const path = 'models/loras/krea2/x.safetensors';
		expect(legacyValuesNeedingLookup([path, path, '', null, undefined], [], new Set())).toEqual([path]);
	});
});
