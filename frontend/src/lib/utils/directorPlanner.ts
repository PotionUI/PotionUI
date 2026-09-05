// Video Director selection/dependency planner (DIR-02).
//
// `validateDirector` (videoDirector.ts) validates the WHOLE document -- every
// shot's own reasons, and every continuation shot's "needs its previous
// shot" check, land in one flat `reasons` list regardless of which shots the
// Shot Console's row checkboxes (`directorCheckedByTab`) actually have
// checked. That makes an unselected, half-finished shot block a perfectly
// generatable selected one, and makes selecting a continuation shot's OWN
// prerequisite (with nothing else checked) get rejected by ITS dependant's
// "needs its previous shot" reason -- see the LTX timeline branch below,
// this module's whole reason for existing.
//
// This module only orders and gates a submission plan; it never mutates the
// stored film and never performs the actual predecessor -> dependant media
// handoff (that stays wherever the wire submission is built).

import {
	toModelessDirectorValue,
	deriveDirectorMode,
	resolveDirectorEdgeAllowances,
	evaluateDirectorTiming,
	validateDirector,
	isSegmentFormMediaReference,
	DEFAULT_TIMELINE_SHOT_ID
} from './videoDirector';
import type {
	VideoDirectorValue,
	DirectorCapabilities,
	DirectorTimelineShot,
	SegmentReference
} from '$lib/types/videoDirector';

export interface DirectorShotReadiness {
	id: string;
	ready: boolean;
	reasons: string[];
}

export interface DirectorSelectionPlan {
	/** Shot ids to actually submit, in dependency order (a predecessor always
	 * precedes its dependant -- the timeline's own shot order already
	 * guarantees this, LTX has no continuation target other than "the
	 * previous shot"). Empty whenever `blockingReasons` is non-empty. */
	shotsToSubmit: string[];
	/** One entry per shot this plan actually considered -- only the checked
	 * shots (or every shot, when the checked set is empty: "the whole film",
	 * same meaning as everywhere else this module's callers use it). A shot
	 * left unchecked is never evaluated at all, so its own problems can never
	 * block a valid checked shot. */
	perShotReadiness: DirectorShotReadiness[];
	/** Every not-ready shot's reasons, flattened, multi-shot entries prefixed
	 * "Shot N: " the same way `validateDirector` already does -- what a
	 * caller shows as the single disabled-button reason or error toast. */
	blockingReasons: string[];
}

function segmentReferenceReasons(
	segments: { references?: SegmentReference[] }[],
	caps: DirectorCapabilities
): string[] {
	const reasons: string[] = [];
	if (caps.references === 'per_shot') {
		const badField = segments
			.flatMap((s) => s.references ?? [])
			.find((ref) => isSegmentFormMediaReference(ref) && !caps.referenceFields.includes(ref.form_media.field));
		if (badField) reasons.push("A per-shot reference points at a field this mode doesn't declare as a reference field");
	} else if (segments.some((s) => s.references && s.references.length > 0)) {
		reasons.push('Per-shot references are not supported in this mode');
	}
	return reasons;
}

/** A single LTX timeline shot's OWN reasons -- everything `validateDirector`'s
 * non-chain `director` branch checks per shot, minus the continuation check
 * (handled by the caller, which alone knows about selection/`runs`). */
function timelineShotOwnReasons(
	value: VideoDirectorValue,
	caps: DirectorCapabilities,
	shot: DirectorTimelineShot,
	edgeAllowances: ReturnType<typeof resolveDirectorEdgeAllowances>
): string[] {
	const reasons: string[] = [];
	const timing = evaluateDirectorTiming(shot.duration, value.timeline.fps, {
		maxDuration: caps.maxDuration,
		maxFrames: caps.maxFrames
	});
	for (const error of Object.values(timing.fieldErrors)) reasons.push(error);

	if (!value.global_prompt.trim() && !shot.segments.some((seg) => seg.text.trim())) {
		reasons.push('Missing prompt');
	}
	if (shot.keyframes.some((k) => !k.media)) reasons.push('Keyframe missing media');
	if (shot.audio.some((a) => !a.media)) reasons.push('Audio segment missing media');
	if (shot.ic_lora.some((e) => !e.lora)) reasons.push('IC-LoRA entry missing a LoRA');

	const cap = caps.modes.director;
	if (cap?.maxKeyframes != null && shot.keyframes.length > cap.maxKeyframes) {
		reasons.push(`Too many keyframes (max ${cap.maxKeyframes})`);
	}
	if (!edgeAllowances.leadingEdgeAllowed && shot.keyframes.some((k) => k.role === 'first')) {
		reasons.push('This mode has no start-frame slot');
	}
	if (!edgeAllowances.trailingEdgeAllowed && shot.keyframes.some((k) => k.role === 'last')) {
		reasons.push('This mode has no end-frame slot');
	}
	if (!edgeAllowances.freePlacementAllowed && shot.keyframes.some((k) => k.role === 'free')) {
		reasons.push('Free keyframe placement is not supported in this mode');
	}
	reasons.push(...segmentReferenceReasons(shot.segments, caps));
	return reasons;
}

function wholeDocumentPlan(
	rawValue: VideoDirectorValue,
	caps: DirectorCapabilities,
	runs: Record<string, { status: string }> | null | undefined,
	allIds: string[],
	checked: Set<string>
): DirectorSelectionPlan {
	const validation = validateDirector(rawValue, caps, runs);
	const selected = checked.size > 0 ? allIds.filter((id) => checked.has(id)) : allIds;
	return {
		shotsToSubmit: validation.ok ? selected : [],
		perShotReadiness: allIds.map((id) => ({ id, ready: validation.ok, reasons: validation.reasons })),
		blockingReasons: validation.reasons
	};
}

/**
 * Plans a scoped Video Director submission: which shots actually go out (in
 * dependency order), and per-shot/aggregate readiness for exactly the shots
 * under consideration. `checkedShotIds` empty means "the whole film" (same
 * convention as `buildDirectorSubmission`'s `checkedShotIds` and
 * `directorCheckedByTab`'s "no rows checked" default).
 *
 * A chain (`segmentRouting`) document, and a single-shot t2v/i2v/flf
 * document, keep exactly today's whole-document `validateDirector` gating --
 * only the LTX (non-routed, potentially multi-shot) `director` timeline gets
 * independent per-shot readiness, since it is the only shape where an
 * unselected sibling shot's own problems have no business blocking a
 * selected one.
 */
export function planDirectorSelection(
	rawValue: VideoDirectorValue,
	caps: DirectorCapabilities,
	runs: Record<string, { status: string }> | null | undefined,
	checkedShotIds: Iterable<string>
): DirectorSelectionPlan {
	const value = toModelessDirectorValue(rawValue, caps);
	const mode = deriveDirectorMode(value, caps);
	const checked = new Set(checkedShotIds);

	if (mode !== 'director') {
		// t2v/i2v/flf: one implicit shot, no independent selection semantics --
		// unchanged whole-document behaviour under a stable synthetic id.
		return wholeDocumentPlan(rawValue, caps, runs, [DEFAULT_TIMELINE_SHOT_ID], checked);
	}

	if (caps.segmentRouting) {
		// Wan/H3 routed chain -- keeps whole-document validation exactly as
		// today (compile_shot_plan re-derives every position-dependent value
		// server-side against the FULL film; the client never filters segments).
		const allIds = value.chain.segments.map((s) => s.id);
		return wholeDocumentPlan(rawValue, caps, runs, allIds, checked);
	}

	// LTX timeline: independent per-shot readiness.
	const shots = value.timeline.shots;
	if (shots.length === 0) {
		return { shotsToSubmit: [], perShotReadiness: [], blockingReasons: ['At least one shot is required'] };
	}

	const selection = checked.size > 0 ? checked : new Set(shots.map((s) => s.id));
	const multi = shots.length > 1;
	const edgeAllowances = resolveDirectorEdgeAllowances(caps);

	const perShotReadiness: DirectorShotReadiness[] = [];
	const blockingReasons: string[] = [];

	shots.forEach((shot, i) => {
		if (!selection.has(shot.id)) return;
		const reasons = timelineShotOwnReasons(value, caps, shot, edgeAllowances);

		if (shot.continue_from_previous && i > 0) {
			const predecessorId = shots[i - 1].id;
			const predecessorDone = runs?.[predecessorId]?.status === 'done';
			const predecessorSelected = selection.has(predecessorId);
			// Predecessor selected-but-not-done is a scheduling state (the plan
			// submits it first below, in the timeline's own shot order) -- not a
			// validation error. Only a predecessor that is neither done nor
			// selected leaves this shot with nothing to inherit from.
			if (!predecessorDone && !predecessorSelected) {
				reasons.push('needs its previous shot');
			}
		}

		const ready = reasons.length === 0;
		perShotReadiness.push({ id: shot.id, ready, reasons });
		if (!ready) {
			const prefix = multi ? `Shot ${i + 1}: ` : '';
			for (const reason of reasons) blockingReasons.push(prefix + reason);
		}
	});

	// The timeline's own shot order already places every predecessor before
	// its dependant, so filtering it (rather than reordering the selection)
	// is enough to keep the plan in dependency order.
	const shotsToSubmit = blockingReasons.length === 0 ? shots.filter((s) => selection.has(s.id)).map((s) => s.id) : [];

	return { shotsToSubmit, perShotReadiness, blockingReasons };
}
