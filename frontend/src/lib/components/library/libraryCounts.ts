import { writable, type Writable } from 'svelte/store';

export interface LibraryCountsStore<S extends string> {
	counts: Writable<Partial<Record<S, number>>>;
	setCount: (section: S, count: number) => void;
}

export function createLibraryCounts<S extends string>(): LibraryCountsStore<S> {
	const counts = writable<Partial<Record<S, number>>>({});

	function setCount(section: S, count: number) {
		counts.update((current) => (current[section] === count ? current : { ...current, [section]: count }));
	}

	return { counts, setCount };
}
