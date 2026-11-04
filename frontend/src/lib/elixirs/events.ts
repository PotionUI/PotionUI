import { writable } from 'svelte/store';

export interface ElixirRename {
	id: string;
	name: string;
	revision: number;
	updated_at?: string | null;
}

export const allElixirsOpen = writable(false);

export const elixirRenamed = writable<ElixirRename | null>(null);

export interface ElixirChange {
	elixir_id: string;
	revision?: number | null;
	run_id?: string | null;
}

export const elixirChanged = writable<ElixirChange | null>(null);
