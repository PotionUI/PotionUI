// Shared, pure reducer helpers for updating `Tab.directorRuns` off an
// incoming generation WebSocket message (PLAN.md §C W3). `directorRunLinks`
// (generationId -> shot id(s), populated at submit time in
// routes/generate/+page.svelte) is how a message carrying only a
// `generation_id` gets routed back to the shot(s) it covers -- several shot
// ids can share one `generationId` (a chain/H3 film run submits its checked
// span as ONE request). Every message-type handler in this directory that
// touches `directorRuns` goes through these so the merge logic (only ever
// updating an id that already has a run record; never inventing one) lives
// in one place.
import type { Tab, DirectorRunState } from '$lib/types/tabs';

/** The shot id(s) `generationId` covers, or `null` when this generation
 *  isn't a Video Director run at all (the overwhelmingly common case -- most
 *  generations carry no Director document). */
export function directorShotIdsFor(
	tab: Pick<Tab, 'directorRunLinks'>,
	generationId: string | undefined
): string[] | null {
	if (!generationId) return null;
	const ids = tab.directorRunLinks?.[generationId];
	return ids && ids.length > 0 ? ids : null;
}

/** `generation_status` -- every message while a run is in flight flips its
 *  covered shot(s) to 'generating' with the message's progress fraction.
 *  Deliberately uniform across every shot a multi-shot chain/H3 film run
 *  covers (rather than singling out the `segment_id` the message names as
 *  "currently rendering"): the pipe only reports which segment PROGRESS
 *  currently belongs to, never that an earlier one in the span has actually
 *  finished, so there is no reliable per-shot 'done' signal until the whole
 *  generation completes -- the console's 'continuous render' badge already
 *  tells the user these rows are bundled together. */
export function withDirectorRunGenerating(
	tab: Pick<Tab, 'directorRuns'>,
	shotIds: string[],
	progress: number | null
): Record<string, DirectorRunState> {
	const runs = { ...(tab.directorRuns || {}) };
	for (const id of shotIds) {
		const existing = runs[id];
		if (!existing) continue;
		runs[id] = { ...existing, status: 'generating', progress };
	}
	return runs;
}

/** `generation_complete` / `generation_error` / `generation_cancelled` --
 *  resolves every shot the generation covered to a terminal state.
 *  `posterUrl` (from the gallery output, when known) only ever WRITES on a
 *  'done' resolution; an omitted/null value leaves whatever a prior
 *  `gallery_update` already set untouched (never clobbers it back to null). */
export function withDirectorRunTerminal(
	tab: Pick<Tab, 'directorRuns'>,
	shotIds: string[],
	status: 'done' | 'failed',
	posterUrl: string | null,
	finishedAt: number
): Record<string, DirectorRunState> {
	const runs = { ...(tab.directorRuns || {}) };
	for (const id of shotIds) {
		const existing = runs[id];
		if (!existing) continue;
		runs[id] = {
			...existing,
			status,
			progress: status === 'done' ? 1 : existing.progress,
			finishedAt,
			posterUrl: status === 'done' ? (posterUrl ?? existing.posterUrl) : existing.posterUrl
		};
	}
	return runs;
}

/** `gallery_update` -- the final output(s) arriving ahead of/alongside
 *  `generation_complete` (the existing convention every other handler in
 *  this directory already relies on, see complete.ts's own lead-item read).
 *  Only ever sets `posterUrl`, never a status -- that's `generation_status`/
 *  `generation_complete`'s job. */
export function withDirectorRunPoster(
	tab: Pick<Tab, 'directorRuns'>,
	shotIds: string[],
	posterUrl: string
): Record<string, DirectorRunState> {
	const runs = { ...(tab.directorRuns || {}) };
	for (const id of shotIds) {
		const existing = runs[id];
		if (!existing) continue;
		runs[id] = { ...existing, posterUrl };
	}
	return runs;
}
