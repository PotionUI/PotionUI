import { writable } from 'svelte/store';

export interface ElixirRename {
	id: string;
	name: string;
	revision: number;
	updated_at?: string | null;
}

export const allElixirsOpen = writable(false);

export const elixirRenamed = writable<ElixirRename | null>(null);
