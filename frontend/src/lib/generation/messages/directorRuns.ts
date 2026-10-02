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
//
// A shot can be re-submitted under a NEW generation id while an OLDER
// generation covering that same shot is still in flight (e.g. Retry before
// the first attempt's terminal event has arrived) -- `directorRunLinks` keeps
// both generation ids mapped to the shot, but `directorRuns[shotId]` only
// ever tracks the latest one it was resubmitted under
// (`buildDirectorRunEntries` in +page.svelte stamps a fresh `generationId`
// on the run at submit time). Every function below requires
// `existing.generationId === generationId` before touching a run, so a
// belated event from the SUPERSEDED generation can't clobber the shot's
// newer run.
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

export function withDirectorRunGenerating(
	tab: Pick<Tab, 'directorRuns'>,
	shotIds: string[],
	progress: number | null,
	generationId: string | undefined
): Record<string, DirectorRunState> {
	const runs = { ...(tab.directorRuns || {}) };
	const reportedByShot = shotIds.some((id) => runs[id]?.generationId === generationId && runs[id]?.reported);
	if (reportedByShot) return runs;
	for (const id of shotIds) {
		const existing = runs[id];
		if (!existing || existing.generationId !== generationId) continue;
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
	finishedAt: number,
	generationId: string | undefined,
	message: string | null = null
): Record<string, DirectorRunState> {
	const runs = { ...(tab.directorRuns || {}) };
	for (const id of shotIds) {
		const existing = runs[id];
		if (!existing || existing.generationId !== generationId) continue;
		if (existing.reported && (existing.status === 'done' || existing.status === 'failed')) continue;
		runs[id] = {
			...existing,
			status,
			progress: status === 'done' ? 1 : existing.progress,
			finishedAt,
			posterUrl: status === 'done' ? (posterUrl ?? existing.posterUrl) : existing.posterUrl,
			message: status === 'failed' ? (message ?? undefined) : undefined
		};
	}
	return runs;
}

export type DirectorShotStatus = 'queued' | 'generating' | 'done' | 'failed' | 'skipped' | 'cancelled';

export interface DirectorShotUpdate {
	shotId: string;
	status: DirectorShotStatus;
	progress: number | null;
	message: string | null;
	outputUrl: string | null;
	outputPath: string | null;
	nsfw: boolean;
}

const SHOT_STATUSES: readonly string[] = ['queued', 'generating', 'done', 'failed', 'skipped', 'cancelled'];

export function parseDirectorShotUpdate(message: Record<string, any>): DirectorShotUpdate | null {
	const shotId = message.shot_id ?? message.data?.shot_id;
	const status = message.status ?? message.data?.status;
	if (typeof shotId !== 'string' || !shotId || typeof status !== 'string' || !SHOT_STATUSES.includes(status)) return null;
	const progress = message.progress ?? message.data?.progress;
	const reason = message.message ?? message.data?.message;
	const output = message.output_url ?? message.data?.output_url;
	const path = message.output_path ?? message.data?.output_path;
	return {
		shotId,
		status: status as DirectorShotStatus,
		progress: typeof progress === 'number' && Number.isFinite(progress) ? Math.max(0, Math.min(1, progress)) : null,
		message: typeof reason === 'string' && reason.trim() ? reason.trim() : null,
		outputUrl: typeof output === 'string' && output ? output : null,
		outputPath: typeof path === 'string' && path ? path : null,
		nsfw: (message.nsfw ?? message.data?.nsfw) === true
	};
}

const SKIPPED_TEXT = 'Not made because an earlier shot did not finish.';
const CANCELLED_TEXT = 'Stopped before it finished.';

export function withDirectorShotUpdate(
	tab: Pick<Tab, 'directorRuns'>,
	update: DirectorShotUpdate,
	finishedAt: number,
	generationId: string | undefined
): Record<string, DirectorRunState> {
	const runs = { ...(tab.directorRuns || {}) };
	const existing = runs[update.shotId];
	if (!existing || existing.generationId !== generationId) return runs;
	if (existing.status === 'done' && existing.reported && update.status !== 'done') return runs;
	switch (update.status) {
		case 'queued':
			runs[update.shotId] = { ...existing, status: 'queued', progress: null, reported: true, message: null };
			break;
		case 'generating':
			runs[update.shotId] = { ...existing, status: 'generating', progress: update.progress, reported: true, message: null };
			break;
		case 'done':
			runs[update.shotId] = {
				...existing,
				status: 'done',
				progress: 1,
				finishedAt,
				posterUrl: update.outputUrl ?? existing.posterUrl,
				flagged: update.nsfw || undefined,
				outputPath: update.outputPath ?? existing.outputPath,
				reported: true,
				message: null
			};
			break;
		case 'failed':
			runs[update.shotId] = {
				...existing,
				status: 'failed',
				finishedAt,
				reported: true,
				message: update.message ?? 'This shot could not be made.'
			};
			break;
		case 'skipped':
		case 'cancelled':
			runs[update.shotId] = {
				...existing,
				status: 'failed',
				finishedAt,
				reported: true,
				message: update.message ?? (update.status === 'skipped' ? SKIPPED_TEXT : CANCELLED_TEXT)
			};
			break;
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
	posterUrl: string,
	generationId: string | undefined
): Record<string, DirectorRunState> {
	const runs = { ...(tab.directorRuns || {}) };
	for (const id of shotIds) {
		const existing = runs[id];
		if (!existing || existing.generationId !== generationId || existing.reported) continue;
		runs[id] = { ...existing, posterUrl };
	}
	return runs;
}

/** Drops a terminated generation's `directorRunLinks` entry -- a link only
 *  ever fires one terminal event for its generation id, so leaving it would
 *  accumulate stale entries on the tab forever (harmless for a run that
 *  matched above, dead weight for one superseded by a resubmission). */
export function withoutDirectorRunLink(
	tab: Pick<Tab, 'directorRunLinks'>,
	generationId: string | undefined
): Record<string, string[]> {
	const links = { ...(tab.directorRunLinks || {}) };
	if (generationId) delete links[generationId];
	return links;
}
