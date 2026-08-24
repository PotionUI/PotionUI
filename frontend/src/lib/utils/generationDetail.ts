import type { GenerationHistoryItem } from '$lib/types/history';

/**
 * Which generation object the details modal should render.
 *
 * The history grid hands the modal a list-item, and `GET /api/generations/history`
 * omits `segments` — only the detail endpoint returns them. So once the detail for
 * this id has loaded we prefer it; until then the list item gives us an instant
 * first paint.
 */
export function pickActiveGeneration(
	provided: GenerationHistoryItem | null,
	loaded: GenerationHistoryItem | null,
	activeGenerationId: string
): GenerationHistoryItem | null {
	if (loaded && loaded.id === activeGenerationId) return loaded;
	return provided;
}

/**
 * Whether the modal still needs to fetch the detail payload.
 *
 * Keyed on the absence of `segments` rather than the absence of a generation
 * object: a list item is a complete-looking generation that simply has no
 * segments on it. The detail endpoint returns `segments: []` for rows predating
 * migration 065, so `undefined` flips to `[]` after one fetch and this settles.
 *
 * `requestedDetailId` guards the reactive statement against re-firing while the
 * request is in flight.
 */
export function needsDetailFetch(
	isOpen: boolean,
	activeGenerationId: string,
	activeGeneration: GenerationHistoryItem | null,
	requestedDetailId: string | undefined
): boolean {
	if (!isOpen || !activeGenerationId) return false;
	if (requestedDetailId === activeGenerationId) return false;
	return activeGeneration?.segments === undefined;
}
