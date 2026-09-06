/**
 * Pure request-assembly: turns a tab's raw form state plus its Video/Music
 * Director editor value into the `form_data` payload (and, for Video
 * Director, the representative prompt array and shot bookkeeping) a real
 * generation submits - the SAME assembly `routes/generate/+page.svelte`'s
 * `startGeneration()` calls, extracted so it isn't inlined there.
 *
 * Deliberately pure: no toasts, no store writes, no network calls. A
 * validation/availability failure (a Video Director document not ready to
 * submit, or a continuation shot whose predecessor output isn't available
 * yet) comes back as `{ kind: 'video', ok: false, reason }` rather than a
 * side effect - the caller decides what to do with it (Generate shows a
 * toast and aborts).
 */
import type { PromptPair } from '$lib/types/api';
import type { DirectorRunState } from '$lib/types/tabs';
import type { DirectorCapabilities, VideoDirectorValue, VideoDirectorWireDoc, DirectorMediaValue } from '$lib/types/videoDirector';
import type { MusicDirectorCapabilities, MusicDirectorValue } from '$lib/types/musicDirector';
import type { PredecessorOutputLike } from '$lib/utils/directorContinuation';
import {
	normalizeDirectorValue,
	buildDirectorSubmission,
	representativeDirectorPrompt,
	dereferenceFormMediaRefs
} from '$lib/utils/videoDirector';
import { planDirectorSelection } from '$lib/utils/directorPlanner';
import { resolvePredecessorFrame } from '$lib/utils/directorContinuation';
import { directorPredecessorShotId } from '$lib/utils/directorInputIdentity';
import { normalizeMusicDirectorValue, buildMusicDirectorSubmission } from '$lib/utils/musicDirector';

export interface DirectorAssemblyInput {
	/** The tab's raw form_data BEFORE any Director document is embedded -
	 *  `currentTab.formData` at the generate page. */
	formData: Record<string, unknown>;

	videoDirectorActive: boolean;
	videoDirectorCaps: DirectorCapabilities | null;
	videoDirectorValue: VideoDirectorValue | null | undefined;
	directorRuns: Record<string, DirectorRunState> | null | undefined;
	/** The console's own transient row-checkbox selection - empty means "the
	 *  whole film". */
	directorChecked: Set<string>;
	/** Fresh per-generation-id output snapshot (`peekGenerationOutputs` per
	 *  `directorRuns` entry) - a live read the CALLER gathers, since this
	 *  module stays pure. `null` when there is nothing to resolve against
	 *  (no active Director document, or no continuation shot needs it). */
	predecessorOutputs: Record<string, PredecessorOutputLike> | null;

	musicDirectorActive: boolean;
	musicDirectorCaps: MusicDirectorCapabilities | null;
	musicDirectorValue: MusicDirectorValue | null | undefined;
}

export type DirectorAssemblyResult =
	| { kind: 'inactive' }
	| {
			kind: 'video';
			ok: true;
			formData: Record<string, unknown>;
			prompts: PromptPair[];
			primaryShotIds: string[];
			remainingShotIds: string[];
			directorValue: VideoDirectorValue;
	  }
	| { kind: 'video'; ok: false; reason: string }
	| { kind: 'music'; formData: Record<string, unknown>; prompts: PromptPair[] };

/**
 * Assembles the Director-aware `form_data` (and, for Video Director, the
 * prompt array + shot bookkeeping) exactly as a real generation submission
 * would - Video Director takes priority over Music Director, mirroring the
 * generate page's own `if (videoDirectorActive) {...} else if
 * (musicDirectorActive) {...}` branch order. Neither active yields `{ kind:
 * 'inactive' }`, in which case the caller's own `formData` (untouched) is
 * what a real submission would send.
 */
export function assembleDirectorRequest(input: DirectorAssemblyInput): DirectorAssemblyResult {
	if (input.videoDirectorActive && input.videoDirectorCaps) {
		const caps = input.videoDirectorCaps;
		const doc = normalizeDirectorValue(input.videoDirectorValue, caps);
		const plan = planDirectorSelection(doc, caps, input.directorRuns, input.directorChecked);
		if (plan.blockingReasons.length > 0) {
			return { kind: 'video', ok: false, reason: plan.blockingReasons[0] || 'Video Director is not ready to generate.' };
		}

		const isChainDoc = caps.segmentRouting;
		const targetShotIds = plan.shotsToSubmit;

		let wireDoc: VideoDirectorWireDoc;
		let primaryShotIds: string[];
		let remainingShotIds: string[] = [];

		if (isChainDoc) {
			// Wan/H3 routed chain - ONE generation covers every targeted shot at
			// once; no "remaining shots" follow-up for a chain doc at all.
			wireDoc = buildDirectorSubmission(doc, caps, input.directorChecked)[0];
			primaryShotIds = targetShotIds;
		} else {
			// LTX timeline - one generation PER shot; the primary is always
			// `targetShotIds[0]` (planDirectorSelection already orders every
			// predecessor before its dependant).
			const primaryShotId = targetShotIds[0];
			remainingShotIds = targetShotIds.slice(1);
			const predecessorId = directorPredecessorShotId(doc, caps, primaryShotId);
			let predecessorFrame: DirectorMediaValue | null = null;
			if (predecessorId) {
				const resolved = resolvePredecessorFrame(
					doc,
					caps,
					primaryShotId,
					input.directorRuns,
					input.predecessorOutputs
				);
				if (!resolved.ok) {
					return { kind: 'video', ok: false, reason: resolved.reason };
				}
				predecessorFrame = resolved.media;
			}
			wireDoc = buildDirectorSubmission(
				doc,
				caps,
				new Set([primaryShotId]),
				predecessorFrame ? { [primaryShotId]: predecessorFrame } : undefined
			)[0];
			primaryShotIds = [primaryShotId];
		}

		// A media entry may point at the form's own media-loader field(s)
		// rather than embedding its own copy - resolve those live, right
		// before the request is built. The server contract
		// (form_data.video_director) never sees `form_ref`.
		const { doc: resolvedWireDoc, errors: formRefErrors } = dereferenceFormMediaRefs(wireDoc, input.formData);
		if (formRefErrors.length > 0) {
			return {
				kind: 'video',
				ok: false,
				reason: `Video Director references media that's no longer on the form: ${formRefErrors.join('; ')}`
			};
		}

		return {
			kind: 'video',
			ok: true,
			formData: { ...input.formData, video_director: resolvedWireDoc },
			prompts: [{ positive: representativeDirectorPrompt(doc, caps), negative: doc.negative_prompt || '' }],
			primaryShotIds,
			remainingShotIds,
			directorValue: doc
		};
	}

	if (input.musicDirectorActive && input.musicDirectorCaps) {
		const doc = normalizeMusicDirectorValue(input.musicDirectorValue, input.musicDirectorCaps);
		const wireDoc = buildMusicDirectorSubmission(doc, input.musicDirectorCaps);
		return {
			kind: 'music',
			formData: { ...input.formData, music_director: wireDoc },
			prompts: [{ positive: doc.description, negative: '' }]
		};
	}

	return { kind: 'inactive' };
}
