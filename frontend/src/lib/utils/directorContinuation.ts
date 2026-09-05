// Resolves the actual media a `continue_from_previous` LTX timeline shot
// inherits from its predecessor -- the piece `buildTimelineShotWireDoc`
// (videoDirector.ts) was missing entirely: readiness (`validateDirector`'s
// "needs its previous shot", `directorPlanner.ts`) only ever checked the
// predecessor's run STATUS, never produced anything the wire doc could
// actually carry, so a "ready" continuation shot still submitted with
// `media: []` -- a fresh cut despite the join (see this module's own
// docstring on `resolvePredecessorFrame`).
//
// What gets referenced: the PREDECESSOR SHOT's own run (`directorPredecessorShotId`,
// same identity `DirectorRunState.predecessorRef` is keyed by -- see
// directorInputIdentity.ts) -- never the first frame of anything, never a
// thumbnail alone, and never a DIFFERENT run that happens to share a
// generation id. `outputsById` (keyed by GENERATION id, GenerationOutputs'
// own key -- see generationOutputs.ts) is tried first, since it is the
// generation's own rendered video metadata; `run.posterUrl`
// (DirectorRunState, persisted with the tab) is the fallback once that
// transient cache has been drained by `generation_complete` (complete.ts
// calls `takeGenerationOutputs` unconditionally, so by the time a later
// caller re-resolves a shot's predecessor after a reload or a delayed
// dependency-runner step, only `posterUrl` survives).
//
// The value this resolves to is the predecessor's rendered OUTPUT VIDEO,
// typed `video` on the wire `MediaRef` -- once a video-typed `first` entry
// reaches a timeline-style shot, `derive_ltx_media_fields`
// (src/features/video_director/normalize.py) treats it as a continuation
// reference and extracts its last frame server-side (frame_extract, cached
// under `generations/_director_continuation`) rather than dropping it; this
// module's own job stops at wiring the real join through end-to-end (the
// request now names WHICH predecessor output it depends on, instead of
// silently omitting it).
import type { VideoDirectorValue, DirectorCapabilities, DirectorMediaValue } from '$lib/types/videoDirector';
import { directorPredecessorShotId } from './directorInputIdentity';

/** The subset of `DirectorRunState` (types/tabs.ts) this module reads --
 *  accepting a structural slice, not the full type, so a caller can pass
 *  `Tab.directorRuns` directly without this module importing it. */
export interface PredecessorRunLike {
	status: string;
	generationId: string;
	posterUrl?: string | null;
}

/** The subset of a generation's cached gallery outputs
 *  (`GenerationGalleryOutputs`, generation/messages/generationOutputs.ts)
 *  this module reads -- same structural-slice reasoning as `PredecessorRunLike`. */
export interface PredecessorOutputLike {
	videos?: ReadonlyArray<{ url?: string | null; originalUrl?: string | null } | null | undefined> | null;
}

export interface ResolvedPredecessorFrame {
	/** The predecessor's rendered output, as a wire `MediaRef` -- video-typed
	 *  (see this file's header comment on the backend gap). */
	media: DirectorMediaValue;
	/** Which run this was resolved from -- the same shape
	 *  `DirectorRunState.predecessorRef` stores, so a caller stamping that
	 *  field can reuse this verbatim. */
	predecessor: { shotId: string; generationId: string; outputKey: string };
}

export type ResolvePredecessorFrameResult = ({ ok: true } & ResolvedPredecessorFrame) | { ok: false; reason: string };

function outputUrlFor(
	run: PredecessorRunLike,
	outputsById: Record<string, PredecessorOutputLike> | null | undefined
): string | null {
	const cached = outputsById?.[run.generationId]?.videos?.[0];
	return cached?.originalUrl || cached?.url || run.posterUrl || null;
}

/**
 * Resolves the media a `continue_from_previous` shot should inherit from its
 * predecessor, or a specific reason it can't yet.
 *
 * `doc` is the same normalized `VideoDirectorValue` shape
 * `directorPredecessorShotId`/`buildDirectorRunEntries` already consume (no
 * `toModelessDirectorValue` projection needed -- an LTX document's
 * `timeline.shots` is already in that shape). `runs` is keyed by SHOT id
 * (`Tab.directorRuns`), `outputsById` by GENERATION id
 * (`GenerationGalleryOutputs`'s own key) -- deliberately different keys,
 * mirrored from how those two stores actually index their data.
 */
export function resolvePredecessorFrame(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	shotId: string,
	runs: Record<string, PredecessorRunLike> | null | undefined,
	outputsById?: Record<string, PredecessorOutputLike> | null
): ResolvePredecessorFrameResult {
	const predecessorId = directorPredecessorShotId(doc, caps, shotId);
	if (!predecessorId) {
		return { ok: false, reason: 'This shot does not continue from a previous shot' };
	}

	const run = runs?.[predecessorId];
	if (!run) {
		return { ok: false, reason: 'Its previous shot has not been generated yet' };
	}
	if (run.status === 'failed') {
		return { ok: false, reason: 'Its previous shot failed to generate' };
	}
	if (run.status !== 'done') {
		return { ok: false, reason: 'Its previous shot is still generating' };
	}

	const url = outputUrlFor(run, outputsById);
	if (!url) {
		return { ok: false, reason: "Its previous shot's output is not available" };
	}

	const media: DirectorMediaValue = { path: url, relative_path: url, url, type: 'video' };
	return { ok: true, media, predecessor: { shotId: predecessorId, generationId: run.generationId, outputKey: predecessorId } };
}
