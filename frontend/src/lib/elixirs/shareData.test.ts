import { expect, test } from 'vitest';
import { createDataSharer, deepEqual } from './shareData';

test('deepEqual compares plain data structurally and everything else by reference', () => {
	const handler = () => {};
	expect(deepEqual({ a: [1, { b: 2 }], h: handler }, { a: [1, { b: 2 }], h: handler })).toBe(true);
	expect(deepEqual({ a: [1, 2] }, { a: [1, 3] })).toBe(false);
	expect(deepEqual({ a: 1 }, { a: 1, b: undefined })).toBe(false);
	expect(deepEqual([1], { 0: 1 })).toBe(false);
	expect(deepEqual({ h: () => {} }, { h: () => {} })).toBe(false);
	const set = new Set(['a']);
	expect(deepEqual({ set }, { set })).toBe(true);
	expect(deepEqual({ set }, { set: new Set(['a']) })).toBe(false);
});

test('the sharer hands back the earlier object while the data has not changed', () => {
	const sharer = createDataSharer<{ run: { outputs: Array<{ id: string }> } }>();
	const first = sharer.share('n1', { run: { outputs: [{ id: 'a' }] } });
	const again = sharer.share('n1', { run: { outputs: [{ id: 'a' }] } });
	expect(again).toBe(first);
	const grown = sharer.share('n1', { run: { outputs: [{ id: 'a' }, { id: 'b' }] } });
	expect(grown).not.toBe(first);
	expect(sharer.share('n1', { run: { outputs: [{ id: 'a' }, { id: 'b' }] } })).toBe(grown);
});

test('nodes never share with each other', () => {
	const sharer = createDataSharer<{ v: number }>();
	const one = sharer.share('n1', { v: 1 });
	const two = sharer.share('n2', { v: 1 });
	expect(one).not.toBe(two);
	expect(sharer.share('n1', { v: 1 })).toBe(one);
	expect(sharer.share('n2', { v: 1 })).toBe(two);
});
