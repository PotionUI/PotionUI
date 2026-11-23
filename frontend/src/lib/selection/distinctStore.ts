import { readable, type Readable } from 'svelte/store';

export function distinctPick<S, T>(source: Readable<S>, pick: (state: S) => T): Readable<T> {
	return readable<T>(undefined as T, (set) => {
		let first = true;
		let last: T;
		return source.subscribe((state) => {
			const next = pick(state);
			if (!first && next === last) return;
			first = false;
			last = next;
			set(next);
		});
	});
}
