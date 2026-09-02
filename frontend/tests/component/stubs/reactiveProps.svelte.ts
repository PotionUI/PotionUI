/**
 * `mount()` props are only reactive if the object passed as `props` is
 * itself a `$state` proxy (plain object literals are read once, at mount
 * time) - this factory gives imperative-mount tests a live props object to
 * hand to `mount()` and a setter to mutate it afterwards. Runes only work in
 * a `.svelte`/`.svelte.ts` module, hence the split from the `.test.ts` file.
 */
export function reactiveProps<T extends Record<string, unknown>>(initial: T) {
	const state = $state(initial);
	return {
		get props(): T {
			return state;
		},
		set(next: Partial<T>) {
			Object.assign(state, next);
		}
	};
}
