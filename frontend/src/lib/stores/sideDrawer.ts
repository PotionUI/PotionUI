import { writable } from 'svelte/store';

export type SideDrawerName = 'sessions' | 'formulas';

const current = writable<SideDrawerName | null>(null);

export const sideDrawer = {
	subscribe: current.subscribe,
	open(name: SideDrawerName) {
		current.set(name);
	},
	close(name: SideDrawerName) {
		current.update((value) => (value === name ? null : value));
	}
};
