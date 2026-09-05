import { describe, it, expect } from 'vitest';
import { planDirectorSelection } from './directorPlanner';
import { parseDirectorCapabilities, normalizeDirectorValue, validateDirector } from './videoDirector';
import type { DirectorTimelineShot, VideoDirectorValue } from '$lib/types/videoDirector';

// LTX-style timeline director (no segment_routing) -- same fixture shape as
// videoDirector.test.ts's own RAW_CAPS.
const RAW_CAPS = {
	preset_modes: ['video'],
	modes: {
		t2v: {},
		i2v: {},
		flf: {},
		director: { audio: true, ic_lora: true, max_keyframes: 8 }
	},
	limits: { default_duration: 5, default_fps: 24, max_duration: 30 }
};

// Wan-style routed chain director (segment_routing: true).
const WAN_RAW_CAPS = {
	preset_modes: ['video'],
	segment_routing: true,
	modes: {
		director: { keyframes: 'first_only', max_segments: 8 }
	},
	limits: { default_duration: 5, default_fps: 16, max_duration: 60 }
};

const caps = parseDirectorCapabilities(RAW_CAPS)!;
const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!;

function shot(id: string, overrides: Partial<DirectorTimelineShot> = {}): DirectorTimelineShot {
	return {
		id,
		duration: 3,
		continue_from_previous: false,
		segments: [{ id: `${id}-seg`, start: 0, end: 3, text: '', prompt_segments: [] }],
		keyframes: [],
		audio: [],
		ic_lora: [],
		...overrides
	};
}

function timelineDoc(shots: DirectorTimelineShot[]): VideoDirectorValue {
	return {
		...normalizeDirectorValue({}, caps),
		mode: 'director',
		global_prompt: 'anchor',
		timeline: { fps: 24, shots }
	};
}

describe('planDirectorSelection: LTX timeline', () => {
	it('an independent valid shot stays ready even when an unselected sibling has an empty keyframe (Codex probe)', () => {
		const a = shot('shot-a');
		const b = shot('shot-b', { keyframes: [{ id: 'k1', start: 0, role: 'first', strength: 1, media: null }] });
		const doc = timelineDoc([a, b]);

		const plan = planDirectorSelection(doc, caps, null, ['shot-a']);

		expect(plan.blockingReasons).toEqual([]);
		expect(plan.shotsToSubmit).toEqual(['shot-a']);
		expect(plan.perShotReadiness).toEqual([{ id: 'shot-a', ready: true, reasons: [] }]);
	});

	it('a selected invalid shot reports its own reason and does not submit', () => {
		const a = shot('shot-a');
		const b = shot('shot-b', { keyframes: [{ id: 'k1', start: 0, role: 'first', strength: 1, media: null }] });
		const doc = timelineDoc([a, b]);

		const plan = planDirectorSelection(doc, caps, null, ['shot-b']);

		expect(plan.perShotReadiness).toEqual([{ id: 'shot-b', ready: false, reasons: ['Keyframe missing media'] }]);
		expect(plan.blockingReasons).toEqual(['Shot 2: Keyframe missing media']);
		expect(plan.shotsToSubmit).toEqual([]);
	});

	it('selecting a continuation shot\'s prerequisite alone is not blocked by the dependant\'s "needs its previous shot"', () => {
		const a = shot('shot-a');
		const b = shot('shot-b', { continue_from_previous: true });
		const doc = timelineDoc([a, b]);

		const plan = planDirectorSelection(doc, caps, null, ['shot-a']);

		expect(plan.blockingReasons).toEqual([]);
		expect(plan.shotsToSubmit).toEqual(['shot-a']);
	});

	it('a continuation shot whose predecessor is done (per runs) is ready even when the predecessor is not selected', () => {
		const a = shot('shot-a');
		const b = shot('shot-b', { continue_from_previous: true });
		const doc = timelineDoc([a, b]);

		const plan = planDirectorSelection(doc, caps, { 'shot-a': { status: 'done' } }, ['shot-b']);

		expect(plan.blockingReasons).toEqual([]);
		expect(plan.perShotReadiness).toEqual([{ id: 'shot-b', ready: true, reasons: [] }]);
		expect(plan.shotsToSubmit).toEqual(['shot-b']);
	});

	it('a continuation shot whose predecessor is selected but not done is a scheduling state, not a validation error, and submits predecessor first', () => {
		const a = shot('shot-a');
		const b = shot('shot-b', { continue_from_previous: true });
		const doc = timelineDoc([a, b]);

		const plan = planDirectorSelection(doc, caps, null, ['shot-a', 'shot-b']);

		expect(plan.blockingReasons).toEqual([]);
		expect(plan.perShotReadiness).toEqual([
			{ id: 'shot-a', ready: true, reasons: [] },
			{ id: 'shot-b', ready: true, reasons: [] }
		]);
		expect(plan.shotsToSubmit).toEqual(['shot-a', 'shot-b']);
	});

	it('a continuation shot whose predecessor is neither done nor selected reports "needs its previous shot" as its own reason', () => {
		const a = shot('shot-a');
		const b = shot('shot-b', { continue_from_previous: true });
		const doc = timelineDoc([a, b]);

		const plan = planDirectorSelection(doc, caps, null, ['shot-b']);

		expect(plan.perShotReadiness).toEqual([{ id: 'shot-b', ready: false, reasons: ['needs its previous shot'] }]);
		expect(plan.blockingReasons).toEqual(['Shot 2: needs its previous shot']);
		expect(plan.shotsToSubmit).toEqual([]);
	});

	it('an empty checked set means the full film -- same shots and order as before, still gated by every shot', () => {
		const a = shot('shot-a');
		const b = shot('shot-b');
		const doc = timelineDoc([a, b]);

		const plan = planDirectorSelection(doc, caps, null, []);

		expect(plan.blockingReasons).toEqual([]);
		expect(plan.shotsToSubmit).toEqual(['shot-a', 'shot-b']);
		expect(plan.perShotReadiness).toEqual([
			{ id: 'shot-a', ready: true, reasons: [] },
			{ id: 'shot-b', ready: true, reasons: [] }
		]);
	});

	it('a single-shot document reports reasons unprefixed, matching validateDirector\'s own single-shot convention', () => {
		// A 'free' keyframe (unlike an unfilled 'first'/'last' edge, which
		// derives as "no edge at all" -- deriveDirectorMode/singleShotEdges)
		// always forces `director` mode -- see videoDirector.test.ts's own
		// equivalent regression test for the same reasoning.
		const a = shot('shot-a', { keyframes: [{ id: 'k1', start: 0, role: 'free', strength: 1, media: null }] });
		const doc = timelineDoc([a]);

		const plan = planDirectorSelection(doc, caps, null, ['shot-a']);

		expect(plan.perShotReadiness).toEqual([{ id: 'shot-a', ready: false, reasons: ['Keyframe missing media'] }]);
		expect(plan.blockingReasons).toEqual(['Keyframe missing media']);
	});

	it('no shots at all is a single blocking reason, not a per-shot one', () => {
		const doc = timelineDoc([]);
		const plan = planDirectorSelection(doc, caps, null, []);
		expect(plan.blockingReasons).toEqual(['At least one shot is required']);
		expect(plan.perShotReadiness).toEqual([]);
		expect(plan.shotsToSubmit).toEqual([]);
	});
});

describe('planDirectorSelection: chain (segment_routing) keeps whole-document validation', () => {
	// Loosely typed like every other raw-chain fixture in videoDirector.test.ts
	// -- `normalizeDirectorValue` takes `unknown`, so a segment literal only
	// needs `id`/`prompt`, not a full `ChainSegment`.
	function chainDoc(overrides: Record<string, unknown> = {}): VideoDirectorValue {
		return normalizeDirectorValue(
			{
				mode: 'director',
				chain: {
					segments: [
						{ id: 'c1', prompt: 'first segment' },
						{ id: 'c2', prompt: '' } // invalid: every segment needs a prompt
					],
					...overrides
				}
			},
			wanCaps
		);
	}

	it('a checked-but-otherwise-fine segment is still blocked by an unrelated invalid segment (unchanged whole-doc behaviour)', () => {
		const doc = chainDoc();
		const wholeDoc = validateDirector(doc, wanCaps, null);
		expect(wholeDoc.ok).toBe(false);

		const plan = planDirectorSelection(doc, wanCaps, null, ['c1']);

		expect(plan.blockingReasons).toEqual(wholeDoc.reasons);
		expect(plan.shotsToSubmit).toEqual([]);
		expect(plan.perShotReadiness).toEqual(
			wholeDoc.reasons.length > 0 ? [
				{ id: 'c1', ready: false, reasons: wholeDoc.reasons },
				{ id: 'c2', ready: false, reasons: wholeDoc.reasons }
			] : []
		);
	});

	it('a fully valid chain document submits the checked segments in the film\'s own order', () => {
		const doc = chainDoc({ segments: [{ id: 'c1', prompt: 'a' }, { id: 'c2', prompt: 'b' }] });
		expect(validateDirector(doc, wanCaps, null).ok).toBe(true);

		const plan = planDirectorSelection(doc, wanCaps, null, ['c2', 'c1']);

		expect(plan.blockingReasons).toEqual([]);
		expect(plan.shotsToSubmit).toEqual(['c1', 'c2']);
	});

	it('an empty checked set plans every segment', () => {
		const doc = chainDoc({ segments: [{ id: 'c1', prompt: 'a' }, { id: 'c2', prompt: 'b' }] });
		const plan = planDirectorSelection(doc, wanCaps, null, []);
		expect(plan.shotsToSubmit).toEqual(['c1', 'c2']);
	});
});

describe('planDirectorSelection: chain (segment_routing) validates the checked span itself', () => {
	// c1 has no keyframe -> t2v (fresh, index 0). c2/c3 have no keyframe and no
	// override -> both derive 'chain' (continue their predecessor) --
	// deriveChainSegmentSubType mirrors normalize.py's derive_segment_sub_type
	// exactly, same as compile_shot_plan sees post-normalize.
	function threeSegmentChain(): VideoDirectorValue {
		return normalizeDirectorValue(
			{
				mode: 'director',
				chain: {
					segments: [
						{ id: 'c1', prompt: 'fresh cut' },
						{ id: 'c2', prompt: 'continues c1' },
						{ id: 'c3', prompt: 'continues c2' }
					]
				}
			},
			wanCaps
		);
	}

	it('a fully valid contiguous span starting on the fresh cut submits as-is', () => {
		const doc = threeSegmentChain();
		const plan = planDirectorSelection(doc, wanCaps, null, ['c1', 'c2', 'c3']);
		expect(plan.blockingReasons).toEqual([]);
		expect(plan.shotsToSubmit).toEqual(['c1', 'c2', 'c3']);
	});

	it('a selection starting on a continuation shot ("c2" alone) is blocked, naming the span action, without being silently widened', () => {
		const doc = threeSegmentChain();
		const plan = planDirectorSelection(doc, wanCaps, null, ['c2']);
		expect(plan.blockingReasons).toEqual([
			'Shot 2 continues the previous shot -- use "Generate previous + this shot", or check its previous shot too'
		]);
		expect(plan.shotsToSubmit).toEqual([]);
		expect(plan.perShotReadiness).toEqual([
			{ id: 'c1', ready: false, reasons: plan.blockingReasons },
			{ id: 'c2', ready: false, reasons: plan.blockingReasons },
			{ id: 'c3', ready: false, reasons: plan.blockingReasons }
		]);
	});

	it('multi-hop: checking c2 and c3 without c1 is still blocked -- c2 itself still starts on a continuation', () => {
		const doc = threeSegmentChain();
		const plan = planDirectorSelection(doc, wanCaps, null, ['c2', 'c3']);
		expect(plan.blockingReasons).toEqual([
			'Shot 2 continues the previous shot -- use "Generate previous + this shot", or check its previous shot too'
		]);
		expect(plan.shotsToSubmit).toEqual([]);
	});

	it('a noncontiguous selection (c1 and c3, skipping c2) is blocked, naming the gap', () => {
		const doc = threeSegmentChain();
		const plan = planDirectorSelection(doc, wanCaps, null, ['c1', 'c3']);
		expect(plan.blockingReasons).toEqual(['Selection has a gap -- shot 2 must be checked too']);
		expect(plan.shotsToSubmit).toEqual([]);
	});

	it('a document that already fails whole-document validation reports that failure, never the selection-span reasons', () => {
		const doc = normalizeDirectorValue(
			{
				mode: 'director',
				chain: { segments: [{ id: 'c1', prompt: 'ok' }, { id: 'c2', prompt: '' }] }
			},
			wanCaps
		);
		const wholeDoc = validateDirector(doc, wanCaps, null);
		expect(wholeDoc.ok).toBe(false);

		// c2 alone would ALSO fail the span check (it continues c1), but the
		// whole-document reason must be reported instead, not both/either.
		const plan = planDirectorSelection(doc, wanCaps, null, ['c2']);
		expect(plan.blockingReasons).toEqual(wholeDoc.reasons);
	});
});

describe('planDirectorSelection: single-shot (t2v/i2v/flf) documents', () => {
	it('delegates to whole-document validateDirector under a stable synthetic id', () => {
		const v = normalizeDirectorValue({ mode: 't2v' }, caps);
		const plan = planDirectorSelection(v, caps, null, []);
		expect(plan.blockingReasons).toEqual(['Missing prompt']);
		expect(plan.shotsToSubmit).toEqual([]);

		const ready = { ...v, global_prompt: 'a dog running' };
		const readyPlan = planDirectorSelection(ready, caps, null, []);
		expect(readyPlan.blockingReasons).toEqual([]);
		expect(readyPlan.shotsToSubmit).toHaveLength(1);
	});
});
