import { describe, expect, it } from 'vitest';
import { get } from 'svelte/store';
import { formulaApplied } from './formulaApplied';

function state(name: string) {
	return { formulaId: name, name, key: 'p|m||', changed: {}, skipped: 0, snapshot: { values: {}, absent: [] } };
}

describe('formulaApplied', () => {
	it('keeps one applied state per tab', () => {
		formulaApplied.set('a', state('one'));
		formulaApplied.set('b', state('two'));
		expect(get(formulaApplied).a.name).toBe('one');
		expect(get(formulaApplied).b.name).toBe('two');
		formulaApplied.clear('a');
		formulaApplied.clear('b');
	});

	it('gives every apply a newer revision and replaces the previous one', () => {
		formulaApplied.set('a', state('one'));
		const first = get(formulaApplied).a.revision;
		formulaApplied.set('a', state('two'));
		const second = get(formulaApplied).a;
		expect(second.name).toBe('two');
		expect(second.revision).toBeGreaterThan(first);
		formulaApplied.clear('a');
	});

	it('clearing a tab removes only that tab and ignores unknown tabs', () => {
		formulaApplied.set('a', state('one'));
		formulaApplied.set('b', state('two'));
		formulaApplied.clear('a');
		formulaApplied.clear('missing');
		expect(Object.keys(get(formulaApplied))).toEqual(['b']);
		formulaApplied.clear('b');
		expect(get(formulaApplied)).toEqual({});
	});
});
