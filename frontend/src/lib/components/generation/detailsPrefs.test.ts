import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import {
	DETAILS_PREFS_KEY,
	detailsPrefs,
	forgetDetailsPrefs,
	loadDetailsPrefs,
	setDetailsPref
} from './detailsPrefs';

const store = new Map<string, string>();

beforeEach(() => {
	store.clear();
	vi.stubGlobal('localStorage', {
		getItem: (key: string) => store.get(key) ?? null,
		setItem: (key: string, value: string) => void store.set(key, value),
		removeItem: (key: string) => void store.delete(key)
	});
	forgetDetailsPrefs();
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('detailsPrefs', () => {
	it('starts empty without stored data', () => {
		loadDetailsPrefs();
		expect(get(detailsPrefs)).toEqual({});
	});

	it('writes and reads per section as a versioned object', () => {
		setDetailsPref('section:segments', true);
		setDetailsPref('list:models', true);
		setDetailsPref('section:segments', false);
		expect(JSON.parse(store.get(DETAILS_PREFS_KEY) as string)).toEqual({
			v: 1,
			sections: { 'section:segments': false, 'list:models': true }
		});
		forgetDetailsPrefs();
		loadDetailsPrefs();
		expect(get(detailsPrefs)).toEqual({ 'section:segments': false, 'list:models': true });
	});

	it('ignores values that are not booleans', () => {
		store.set(DETAILS_PREFS_KEY, JSON.stringify({ v: 1, sections: { a: true, b: 'yes', c: 1 } }));
		loadDetailsPrefs();
		expect(get(detailsPrefs)).toEqual({ a: true });
	});

	it.each([
		['corrupt json', '{nope'],
		['another version', JSON.stringify({ v: 2, sections: { a: true } })],
		['wrong shape', JSON.stringify({ v: 1, sections: null })],
		['not an object', '5']
	])('survives %s', (_name, raw) => {
		store.set(DETAILS_PREFS_KEY, raw);
		expect(() => loadDetailsPrefs()).not.toThrow();
		expect(get(detailsPrefs)).toEqual({});
	});

	it('keeps the choice in memory when storage is blocked', () => {
		vi.stubGlobal('localStorage', {
			getItem: () => {
				throw new Error('blocked');
			},
			setItem: () => {
				throw new Error('blocked');
			}
		});
		expect(() => loadDetailsPrefs()).not.toThrow();
		expect(() => setDetailsPref('x', true)).not.toThrow();
		expect(get(detailsPrefs)).toEqual({ x: true });
	});

	it('reads storage only once until forgotten', () => {
		loadDetailsPrefs();
		store.set(DETAILS_PREFS_KEY, JSON.stringify({ v: 1, sections: { late: true } }));
		loadDetailsPrefs();
		expect(get(detailsPrefs)).toEqual({});
	});
});
