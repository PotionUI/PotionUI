import { writable } from 'svelte/store';

export interface ModeInstallRequest {
	recipeId: string;
	modes: string[];
	id: number;
}

const current = writable<ModeInstallRequest | null>(null);
let nextId = 1;

export const modeInstallRequest = { subscribe: current.subscribe };

export function openModeInstall(recipeId: string, modes: string[] = []): void {
	current.set({ recipeId, modes, id: nextId++ });
}

export function closeModeInstall(): void {
	current.set(null);
}
