import { writable } from 'svelte/store';

export interface KitLoadRequest {
	id: number;
	field: string | null;
}

let counter = 0;

export const kitLoadRequest = writable<KitLoadRequest | null>(null);

export function requestKitLoad(field: string | null = null): void {
	counter += 1;
	kitLoadRequest.set({ id: counter, field });
}

export function clearKitLoadRequest(): void {
	kitLoadRequest.set(null);
}
