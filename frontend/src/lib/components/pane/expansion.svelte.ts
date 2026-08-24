import { storage } from '$lib/utils/storage';

// Persisted expand/collapse set for a PaneTree. `ids` always gets REASSIGNED
// to a fresh Set on every change (never mutated in place) so the `$state`
// signal actually fires — `next.add(id); this.ids = next` on an existing Set
// reference would leave callers reading a stale snapshot.
export class ExpansionState {
	ids = $state<ReadonlySet<string>>(new Set());
	#storageKey?: string;

	constructor(storageKey?: string) {
		this.#storageKey = storageKey;
		if (storageKey) {
			const stored = storage.getJSON<string[]>(storageKey);
			if (Array.isArray(stored)) {
				this.ids = new Set(stored);
			}
		}
	}

	has(id: string): boolean {
		return this.ids.has(id);
	}

	#commit(next: Set<string>): void {
		this.ids = next;
		if (this.#storageKey) {
			storage.setJSON(this.#storageKey, Array.from(next));
		}
	}

	toggle(id: string): void {
		const next = new Set(this.ids);
		if (next.has(id)) {
			next.delete(id);
		} else {
			next.add(id);
		}
		this.#commit(next);
	}

	expand(id: string): void {
		if (this.ids.has(id)) return;
		const next = new Set(this.ids);
		next.add(id);
		this.#commit(next);
	}

	collapse(id: string): void {
		if (!this.ids.has(id)) return;
		const next = new Set(this.ids);
		next.delete(id);
		this.#commit(next);
	}

	expandMany(ids: string[]): void {
		const next = new Set(this.ids);
		let changed = false;
		for (const id of ids) {
			if (!next.has(id)) {
				next.add(id);
				changed = true;
			}
		}
		if (changed) this.#commit(next);
	}
}
