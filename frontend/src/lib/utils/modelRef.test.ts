import { describe, expect, it } from 'vitest';
import { MODEL_REF_PREFIX, refFor, matchesStoredValue, findModelForValue } from './modelRef';

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
