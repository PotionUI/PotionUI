import { expect, test } from 'vitest';
import { get, writable } from 'svelte/store';
import { distinctPick } from './distinctStore';

test('emits only when the picked reference changes', () => {
	const source = writable({ items: [1, 2], selected: [] as string[] });
	const picked = distinctPick(source, (state) => state.items);
	const seen: number[][] = [];
	const stop = picked.subscribe((value) => seen.push(value));
	expect(seen).toHaveLength(1);

	source.update((state) => ({ ...state, selected: ['a'] }));
	source.update((state) => ({ ...state, selected: ['a', 'b'] }));
	expect(seen).toHaveLength(1);

	const replaced = [3];
	source.update((state) => ({ ...state, items: replaced }));
	expect(seen).toHaveLength(2);
	expect(seen[1]).toBe(replaced);
	stop();
});

test('a late subscriber sees the current value', () => {
	const source = writable({ items: ['x'] });
	const picked = distinctPick(source, (state) => state.items);
	expect(get(picked)).toEqual(['x']);
	source.set({ items: ['y'] });
	expect(get(picked)).toEqual(['y']);
});
