import { writable } from 'svelte/store';

/**
 * Session-scoped, shared "click to reveal" state for blurred nsfw files. A
 * plain component-local boolean meant a file revealed on a history card
 * re-blurred the moment the details modal opened a second instance over it;
 * keying reveals here by a stable file identity means any surface that reads
 * this store sees the same reveal. Never persisted - resets on reload.
 */

function createNsfwRevealStore() {
	const { subscribe, update, set } = writable<Set<string>>(new Set());

	return {
		subscribe,

		reveal(key: string) {
			update((revealed) => {
				if (revealed.has(key)) return revealed;
				const next = new Set(revealed);
				next.add(key);
				return next;
			});
		},

		reset() {
			set(new Set());
		}
	};
}

export const nsfwRevealStore = createNsfwRevealStore();

/** Stable identity for a file within a generation, for reveal-state keying. */
export function revealKey(
	generationId: string,
	file: { id?: number | string; file_path?: string }
): string {
	if (file.id !== undefined && file.id !== null) return `${generationId}:${file.id}`;
	return `${generationId}:${file.file_path ?? ''}`;
}
