// The rail owns selection for the whole Stage & Rail editor: exactly one
// object is selected at a time, and a later Stage phase reads this store
// rather than the rail re-deriving it per consumer.

import { writable } from 'svelte/store';
import type { RailObjectKind, RailSelectionId } from './railModel';

export const railSelection = writable<RailSelectionId | null>(null);

export function selectRailObject(kind: RailObjectKind, id: string): void {
	railSelection.set({ kind, id });
}

export function clearRailSelection(): void {
	railSelection.set(null);
}

export function isRailObjectSelected(current: RailSelectionId | null, kind: RailObjectKind, id: string): boolean {
	return current != null && current.kind === kind && current.id === id;
}
