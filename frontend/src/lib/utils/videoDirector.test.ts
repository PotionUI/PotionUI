import { describe, it, expect } from 'vitest';
import {
	parseDirectorCapabilities,
	resolveDirectorCapabilities,
	resolveDirectorEdgeAllowances,
	evaluateDirectorTiming,
	createDefaultDirectorValue,
	isDefaultDirectorDocument,
	seedDirectorPromptFromLegacyText,
	normalizeDirectorValue,
	deriveDirectorMode,
	toModelessDirectorValue,
	validateDirector,
	buildDirectorSubmission,
	representativeDirectorPrompt,
	deriveSegmentSubType,
	deriveChainSegmentSubType,
	chainSegmentIsAmbiguous,
	chainKeyframeWindow,
	applyDirectorOperations,
	applyDirectorSegmentPrompt,
	applySetPrompt,
	applySetNegativePrompt,
	isFormMediaRef,
	resolveFormMediaItem,
	resolveDirectorMediaDisplay,
	collectFormMediaOptions,
	formMediaOptionKeys,
	dereferenceFormMediaRefs
} from './videoDirector';
import type { ChainSegment, VideoDirectorValue, VideoDirectorWireDoc } from '$lib/types/videoDirector';
import type { MediaRef } from '$lib/types/tabs';

// The `director` mode is capability-shaped. Two fixtures mirror the two real
// presets rather than one conflated var: Wan declares a routed multi-shot chain
// director (segment_routing: true), LTX a single keyframe/audio timeline
// director (no routing). The retired `chain` mode is folded into `director`.
const WAN_RAW_CAPS = {
	preset_modes: ['video'],
	segment_routing: true,
	modes: {
		t2v: {},
		i2v: {},
		flf: {},
		director: {
			per_segment_loras: true,
			keyframes: 'first_only',
			max_segments: 8,
			max_frames_per_segment: 81,
			tips: ['chain segments']
		}
	},
	limits: { default_duration: 5, default_fps: 16, max_duration: 60 }
};

// A hybrid chain-style director: routed multi-shot segments like Wan, but the
// family can also honour keyframes anywhere along the chain, an audio track,
// and it caps the continuation overlap.
const HYBRID_RAW_CAPS = {
	preset_modes: ['video'],
	segment_routing: true,
	modes: {
		t2v: {},
		i2v: {},
		director: {
			per_segment_loras: true,
			keyframes: 'anywhere',
			audio: true,
			max_keyframes: 4,
			max_segments: 8,
			max_overlap_frames: 6,
			continuation: { source: 'tail_frames', overlap_frames: 4, stitch: true }
		}
	},
	limits: { default_duration: 5, default_fps: 16, max_duration: 60 }
};

const media = (path: string): MediaRef => ({ path });

// LTX-style timeline director (no segment_routing).
const RAW_CAPS = {
	preset_modes: ['video'],
	modes: {
		t2v: {},
		i2v: {},
		flf: {},
		director: { audio: true, ic_lora: true, max_keyframes: 8, tips: ['use keyframes'] },
		bogus_mode: { audio: true }
	},
	limits: { default_duration: 5, default_fps: 24, max_duration: 30 }
};

describe('parseDirectorCapabilities', () => {
	it('returns null for non-object input', () => {
		expect(parseDirectorCapabilities(null)).toBeNull();
		expect(parseDirectorCapabilities(undefined)).toBeNull();
		expect(parseDirectorCapabilities('nope')).toBeNull();
		expect(parseDirectorCapabilities(42)).toBeNull();
	});

	it('returns null when modes is missing or has no valid entries', () => {
		expect(parseDirectorCapabilities({})).toBeNull();
		expect(parseDirectorCapabilities({ modes: {} })).toBeNull();
		expect(parseDirectorCapabilities({ modes: { unknown: {} } })).toBeNull();
	});

	it('parses a full capability document', () => {
		const caps = parseDirectorCapabilities(RAW_CAPS)!;
		expect(caps).not.toBeNull();
		expect(caps.presetModes).toEqual(['video']);
		expect(caps.defaultDuration).toBe(5);
		expect(caps.defaultFps).toBe(24);
		expect(caps.maxDuration).toBe(30);
	});

	it('drops unknown mode keys and orders enabledModes t2v,i2v,flf,director', () => {
		const caps = parseDirectorCapabilities(RAW_CAPS)!;
		expect(caps.enabledModes).toEqual(['t2v', 'i2v', 'flf', 'director']);
		expect((caps.modes as Record<string, unknown>).bogus_mode).toBeUndefined();
	});

	it('orders enabledModes correctly regardless of declaration order', () => {
		const caps = parseDirectorCapabilities({ modes: { director: {}, t2v: {} } })!;
		expect(caps.enabledModes).toEqual(['t2v', 'director']);
	});

	it('applies defaults for booleans, tips, and numeric limits', () => {
		const caps = parseDirectorCapabilities({ modes: { t2v: {} } })!;
		const t2v = caps.modes.t2v!;
		expect(t2v.audio).toBe(false);
		expect(t2v.icLora).toBe(false);
		expect(t2v.perSegmentLoras).toBe(false);
		expect(t2v.tips).toEqual([]);
		expect(t2v.maxKeyframes).toBeNull();
		expect(t2v.maxSegments).toBeNull();
		expect(t2v.maxFramesPerSegment).toBeNull();
		expect(t2v.maxDuration).toBeNull();
		expect(caps.maxFrames).toBeNull();
		expect(t2v.keyframes).toBe('none');
		expect(t2v.continuation).toBeNull();
	});

	it('keyframes default to none unless the mode declares first_only or anywhere', () => {
		expect(parseDirectorCapabilities({ modes: { director: {} } })!.modes.director!.keyframes).toBe('none');
		expect(
			parseDirectorCapabilities({ modes: { director: { keyframes: 'first_only' } } })!.modes.director!.keyframes
		).toBe('first_only');
		expect(
			parseDirectorCapabilities({ modes: { director: { keyframes: 'anywhere' } } })!.modes.director!.keyframes
		).toBe('anywhere');
		expect(
			parseDirectorCapabilities({ modes: { director: { keyframes: 'sometimes' } } })!.modes.director!.keyframes
		).toBe('none');
	});

	it('parses max_overlap_frames, defaulting to null when undeclared or non-numeric', () => {
		expect(parseDirectorCapabilities(HYBRID_RAW_CAPS)!.modes.director!.maxOverlapFrames).toBe(6);
		expect(parseDirectorCapabilities({ modes: { director: {} } })!.modes.director!.maxOverlapFrames).toBeNull();
		expect(
			parseDirectorCapabilities({ modes: { director: { max_overlap_frames: 'six' } } })!.modes.director!.maxOverlapFrames
		).toBeNull();
	});

	it('parses the hybrid chain director capability fields together', () => {
		const caps = parseDirectorCapabilities(HYBRID_RAW_CAPS)!;
		expect(caps.segmentRouting).toBe(true);
		expect(caps.modes.director).toMatchObject({
			keyframes: 'anywhere',
			audio: true,
			maxKeyframes: 4,
			maxOverlapFrames: 6,
			perSegmentLoras: true
		});
	});

	// The Wan and LTX presets declare none of the hybrid keys. Their parse output
	// is pinned whole so a future capability addition cannot quietly change what
	// an existing preset's director mode resolves to.
	it('regression: the Wan and LTX director capabilities parse exactly as before the hybrid keys existed', () => {
		expect(parseDirectorCapabilities(WAN_RAW_CAPS)!.modes.director).toEqual({
			tips: ['chain segments'],
			maxDuration: 60,
			audio: false,
			icLora: false,
			maxKeyframes: null,
			perSegmentLoras: true,
			keyframes: 'first_only',
			maxSegments: 8,
			maxFramesPerSegment: 81,
			defaultSegmentDuration: 5,
			continuation: null,
			maxOverlapFrames: null,
			continuationDisabled: false
		});
		expect(parseDirectorCapabilities(RAW_CAPS)!.modes.director).toEqual({
			tips: ['use keyframes'],
			maxDuration: 30,
			audio: true,
			icLora: true,
			maxKeyframes: 8,
			perSegmentLoras: false,
			keyframes: 'none',
			maxSegments: null,
			maxFramesPerSegment: null,
			defaultSegmentDuration: 5,
			continuation: null,
			maxOverlapFrames: null,
			continuationDisabled: false
		});
	});

	it('parses the LTX timeline director capability fields', () => {
		const caps = parseDirectorCapabilities(RAW_CAPS)!;
		expect(caps.modes.director).toMatchObject({
			audio: true,
			icLora: true,
			maxKeyframes: 8,
			tips: ['use keyframes']
		});
	});

	it('parses the Wan routed-chain director capability fields (incl. continuation)', () => {
		const caps = parseDirectorCapabilities({
			...WAN_RAW_CAPS,
			modes: {
				...WAN_RAW_CAPS.modes,
				director: { ...WAN_RAW_CAPS.modes.director, continuation: { source: 'tail_frames', overlap_frames: 6, stitch: false } }
			}
		})!;
		expect(caps.modes.director).toMatchObject({
			perSegmentLoras: true,
			keyframes: 'first_only',
			maxSegments: 8,
			maxFramesPerSegment: 81,
			tips: ['chain segments'],
			continuation: { source: 'tail_frames', overlapFrames: 6, stitch: false }
		});
	});

	it('falls back to global default_duration/default_fps when limits omitted', () => {
		const caps = parseDirectorCapabilities({ modes: { t2v: {} } })!;
		expect(caps.defaultDuration).toBe(5);
		expect(caps.defaultFps).toBe(24);
		expect(caps.maxDuration).toBeNull();
		expect(caps.maxFrames).toBeNull();
	});

	it('parses the optional global max_frames generator cap', () => {
		const caps = parseDirectorCapabilities({
			modes: { t2v: {} },
			limits: { max_frames: 1001 }
		})!;
		expect(caps.maxFrames).toBe(1001);
	});

	it('presetModes is null when preset_modes is absent', () => {
		const caps = parseDirectorCapabilities({ modes: { t2v: {} } })!;
		expect(caps.presetModes).toBeNull();
	});

	it('segmentRouting is false by default and true only when the raw var declares it', () => {
		expect(parseDirectorCapabilities(RAW_CAPS)!.segmentRouting).toBe(false);
		expect(parseDirectorCapabilities(WAN_RAW_CAPS)!.segmentRouting).toBe(true);
	});

	it('is content-deterministic: identical input parses to deep-equal (though not necessarily reference-equal) results', () => {
		// +page.svelte's memoization (keyed on preset id + JSON of the raw var)
		// relies on this: re-parsing the same raw var must never produce content
		// that differs from the previous parse, even though each call returns a
		// fresh object.
		const a = parseDirectorCapabilities(RAW_CAPS);
		const b = parseDirectorCapabilities(RAW_CAPS);
		expect(a).toEqual(b);
		expect(a).not.toBe(b);
	});
});

// The full MERGED capability shape a real MiniMax-H3 `refs`-mode request
// resolves to (mirrors presets/native/MiniMax-H3/preset.yml's
// `vars.video_director` + `preset_mode_overrides.refs` exactly) -- same
// fixture the stageModel.ts/railModel.ts test suites use.
const H3_REFS_PRESET_RAW = {
	preset_modes: ['video', 'refs'],
	segment_routing: true,
	modes: {
		t2v: {},
		i2v: {},
		flf: {},
		director: {
			keyframes: 'anywhere',
			audio: true,
			max_keyframes: 8,
			max_segments: 6,
			max_frames_per_segment: 345,
			continuation: { source: 'tail_frames', overlap_frames: 17, stitch: true },
			max_overlap_frames: 34
		}
	},
	limits: { default_duration: 5, default_fps: 24, max_duration: 15 },
	preset_mode_overrides: {
		refs: {
			references: 'per_shot',
			reference_fields: ['references', 'reference_videos', 'reference_audios'],
			modes: { director: { keyframes: null, audio: false, continuation: null, max_overlap_frames: null } }
		}
	}
};

// t2v ⇒ nothing; i2v ⇒ leading only; flf ⇒ both edges; free placement ⇒ an
// open lane -- the one rule every model family reduces to. Fixtures below
// isolate each capability shape rather than reusing WAN/HYBRID/RAW_CAPS
// wholesale, so each assertion traces to exactly one declared capability.
describe('resolveDirectorEdgeAllowances', () => {
	const T2V_ONLY = { modes: { t2v: {} } };
	const I2V_ONLY = { modes: { t2v: {}, i2v: {} } };
	const I2V_FLF = { modes: { t2v: {}, i2v: {}, flf: {} } };

	it('t2v-only: no leading/trailing edge, no free placement', () => {
		const caps = parseDirectorCapabilities(T2V_ONLY)!;
		expect(resolveDirectorEdgeAllowances(caps)).toEqual({
			freePlacementAllowed: false,
			leadingEdgeAllowed: false,
			trailingEdgeAllowed: false
		});
	});

	it('i2v declared (timeline, no director): leading only', () => {
		const caps = parseDirectorCapabilities(I2V_ONLY)!;
		expect(resolveDirectorEdgeAllowances(caps)).toEqual({
			freePlacementAllowed: false,
			leadingEdgeAllowed: true,
			trailingEdgeAllowed: false
		});
	});

	it('i2v + flf declared (timeline, no director): both edges, still no free placement', () => {
		const caps = parseDirectorCapabilities(I2V_FLF)!;
		expect(resolveDirectorEdgeAllowances(caps)).toEqual({
			freePlacementAllowed: false,
			leadingEdgeAllowed: true,
			trailingEdgeAllowed: true
		});
	});

	it('real LTX shape (timeline, director declared, max_keyframes with no `keyframes` field): every affordance stays on', () => {
		// The regression this guards: `keyframes` parses to 'none' here, so a
		// naive `keyframes === 'anywhere'` read on timeline routing would turn
		// every affordance off for a preset that ships this way today.
		const caps = parseDirectorCapabilities(RAW_CAPS)!;
		expect(caps.modes.director!.keyframes).toBe('none');
		expect(resolveDirectorEdgeAllowances(caps)).toEqual({
			freePlacementAllowed: true,
			leadingEdgeAllowed: true,
			trailingEdgeAllowed: true
		});
	});

	it('Wan shape (chain, keyframes first_only, flf declared): leading via the chain rule, trailing via flf', () => {
		const caps = parseDirectorCapabilities(WAN_RAW_CAPS)!;
		expect(resolveDirectorEdgeAllowances(caps)).toEqual({
			freePlacementAllowed: false,
			leadingEdgeAllowed: true,
			trailingEdgeAllowed: true
		});
	});

	it('chain keyframes anywhere, flf NOT declared (HYBRID_RAW_CAPS): free placement alone still opens both edges', () => {
		const caps = parseDirectorCapabilities(HYBRID_RAW_CAPS)!;
		expect(caps.enabledModes).not.toContain('flf');
		expect(resolveDirectorEdgeAllowances(caps)).toEqual({
			freePlacementAllowed: true,
			leadingEdgeAllowed: true,
			trailingEdgeAllowed: true
		});
	});

	it('H3 refs override (chain, i2v/flf declared but keyframes forced null -> none): no edge, no lane', () => {
		// i2v/flf being DECLARED never grants a chain leading well on its own --
		// only the `keyframes` capability does (existing Wan/H3 rule). This is
		// what keeps the refs override's wells off despite flf being present.
		const caps = resolveDirectorCapabilities(H3_REFS_PRESET_RAW, 'refs')!;
		expect(caps.enabledModes).toEqual(expect.arrayContaining(['i2v', 'flf']));
		expect(caps.modes.director!.keyframes).toBe('none');
		expect(resolveDirectorEdgeAllowances(caps).leadingEdgeAllowed).toBe(false);
		// The trailing allowance itself is still true (flf is declared) -- it's
		// `chainTrailingWellEligible`'s single-shot gate (stageModel.ts) that
		// keeps a multi-shot refs document from ever rendering the well.
		expect(resolveDirectorEdgeAllowances(caps).trailingEdgeAllowed).toBe(true);
	});
});

describe('evaluateDirectorTiming', () => {
	const ltxLimits = { maxDuration: 40, maxFrames: 1001 };

	it('rounds requested frames half-up, then snaps to the 1 + k*8 lattice with ties down', () => {
		// 5s @ 25fps asks for 125 frames; 121 is nearer on the causal-VAE lattice.
		expect(evaluateDirectorTiming(5, 25, ltxLimits)).toMatchObject({
			requestedFrames: 125,
			frameCount: 121,
			effectiveDuration: 4.84,
			fieldErrors: {}
		});
		// A 5-frame request is exactly between 1 and 9, so ties go down.
		expect(evaluateDirectorTiming(0.2, 25, ltxLimits).frameCount).toBe(1);
		// A 2.5-frame request verifies the backend's explicit half-up policy.
		expect(evaluateDirectorTiming(0.1, 25, ltxLimits).requestedFrames).toBe(3);
	});

	it('matches backend frame-count boundary and fps oracle cases', () => {
		expect(evaluateDirectorTiming(5, 24, ltxLimits)).toMatchObject({
			requestedFrames: 120,
			frameCount: 121,
			effectiveDuration: 121 / 24,
			fieldErrors: {}
		});
		expect(evaluateDirectorTiming(1001, 1, { maxDuration: null, maxFrames: 1001 })).toMatchObject({
			requestedFrames: 1001,
			frameCount: 1001,
			effectiveDuration: 1001,
			fieldErrors: {}
		});
		expect(evaluateDirectorTiming(5, 60, ltxLimits).fieldErrors.fps).toBeUndefined();
		expect(evaluateDirectorTiming(5, 61, ltxLimits).fieldErrors.fps).toContain('between 1 and 60');
	});

	it('rejects a raw frame request above the cap before snapping', () => {
		const result = evaluateDirectorTiming(5, 25, { maxDuration: null, maxFrames: 124 });
		expect(result.requestedFrames).toBe(125);
		expect(result.frameCount).toBeNull();
		expect(result.fieldErrors.duration).toContain('generator cap of 124 frames');
	});

	it('reports invalid duration and fps in their respective fields', () => {
		expect(evaluateDirectorTiming(0, 25, ltxLimits).fieldErrors.duration).toContain('greater than 0');
		expect(evaluateDirectorTiming(5, 61, ltxLimits).fieldErrors.fps).toContain('between 1 and 60');
	});

	it('keeps legacy no-cap presets at their requested frame count without snapping', () => {
		expect(evaluateDirectorTiming(5, 25, { maxDuration: null, maxFrames: null })).toEqual({
			requestedFrames: 125,
			frameCount: null,
			effectiveDuration: null,
			fieldErrors: {}
		});
	});
});

// Case list mirrors tests/features/video_director/test_normalize.py's routing
// tests (derive_segment_sub_type / derive_segment_routing) one-to-one, so the
// frontend badge never resolves a different sub-type than the backend would.
// `chain` here is a per-segment sub-type (a continuation), distinct from the
// retired `chain` MODE.
describe('deriveSegmentSubType', () => {
	it('an explicit override always wins', () => {
		expect(deriveSegmentSubType({ index: 5, hasFirstMedia: true, hasLastMedia: true, override: 't2v' })).toBe('t2v');
	});

	it('first + last media resolves to flf', () => {
		expect(deriveSegmentSubType({ index: 2, hasFirstMedia: true, hasLastMedia: true, override: null })).toBe('flf');
	});

	it('first media alone resolves to i2v', () => {
		expect(deriveSegmentSubType({ index: 0, hasFirstMedia: true, hasLastMedia: false, override: null })).toBe('i2v');
		expect(deriveSegmentSubType({ index: 2, hasFirstMedia: true, hasLastMedia: false, override: null })).toBe('i2v');
	});

	it('a prompt-only first segment (index 0) resolves to a fresh t2v shot', () => {
		expect(deriveSegmentSubType({ index: 0, hasFirstMedia: false, hasLastMedia: false, override: null })).toBe('t2v');
	});

	it('a prompt-only later segment defaults to chain (continuation)', () => {
		expect(deriveSegmentSubType({ index: 1, hasFirstMedia: false, hasLastMedia: false, override: null })).toBe('chain');
		expect(deriveSegmentSubType({ index: 2, hasFirstMedia: false, hasLastMedia: false, override: null })).toBe('chain');
	});

	// test_routing_t2v_single_segment
	it('backend parity: t2v single segment', () => {
		expect(deriveSegmentSubType({ index: 0, hasFirstMedia: false, hasLastMedia: false, override: null })).toBe('t2v');
	});

	// test_routing_i2v_single_segment
	it('backend parity: i2v single segment (first media, index 0)', () => {
		expect(deriveSegmentSubType({ index: 0, hasFirstMedia: true, hasLastMedia: false, override: null })).toBe('i2v');
	});

	// test_routing_director_t2v_opener_then_continuation_needs_both_sets
	it('backend parity: t2v opener then two continuations', () => {
		const segs = [
			{ index: 0, hasFirstMedia: false, hasLastMedia: false },
			{ index: 1, hasFirstMedia: false, hasLastMedia: false },
			{ index: 2, hasFirstMedia: false, hasLastMedia: false }
		];
		expect(segs.map((s) => deriveSegmentSubType({ ...s, override: null }))).toEqual(['t2v', 'chain', 'chain']);
	});

	// test_routing_director_from_start_image_needs_only_i2v_set
	it('backend parity: opener with a start image, then a continuation', () => {
		const segs = [
			{ index: 0, hasFirstMedia: true, hasLastMedia: false },
			{ index: 1, hasFirstMedia: false, hasLastMedia: false }
		];
		expect(segs.map((s) => deriveSegmentSubType({ ...s, override: null }))).toEqual(['i2v', 'chain']);
	});

	// test_routing_per_segment_override_forces_fresh_cut
	it('backend parity: override forces a fresh cut on an otherwise-chain segment', () => {
		const segs = [
			{ index: 0, hasFirstMedia: true, hasLastMedia: false, override: null },
			{ index: 1, hasFirstMedia: false, hasLastMedia: false, override: 't2v' as const }
		];
		expect(segs.map(deriveSegmentSubType)).toEqual(['i2v', 't2v']);
	});
});

function chainSegment(overrides: Partial<ChainSegment> = {}): ChainSegment {
	return {
		id: 'c1',
		prompt: 'a shot',
		prompt_segments: [],
		duration: 3,
		loras: null,
		keyframe: null,
		keyframe_strength: 1,
		last_keyframe: null,
		last_keyframe_strength: 1,
		sub_type_override: null,
		...overrides
	};
}

describe('deriveChainSegmentSubType / chainSegmentIsAmbiguous', () => {
	it('segment 0 without a keyframe is t2v and not ambiguous', () => {
		const seg = chainSegment();
		expect(deriveChainSegmentSubType(seg, 0)).toBe('t2v');
		expect(chainSegmentIsAmbiguous(seg, 0)).toBe(false);
	});

	it('segment 0 with a keyframe is i2v and not ambiguous', () => {
		const seg = chainSegment({ keyframe: media('/start.png') });
		expect(deriveChainSegmentSubType(seg, 0)).toBe('i2v');
		expect(chainSegmentIsAmbiguous(seg, 0)).toBe(false);
	});

	it('a later prompt-only segment defaults to chain and IS ambiguous', () => {
		const seg = chainSegment();
		expect(deriveChainSegmentSubType(seg, 1)).toBe('chain');
		expect(chainSegmentIsAmbiguous(seg, 1)).toBe(true);
	});

	it('a later segment overridden to t2v resolves to t2v (still ambiguous, override picked one side)', () => {
		const seg = chainSegment({ sub_type_override: 't2v' });
		expect(deriveChainSegmentSubType(seg, 1)).toBe('t2v');
		expect(chainSegmentIsAmbiguous(seg, 1)).toBe(true);
	});
});

describe('chainKeyframeWindow', () => {
	it('sums the ROUNDED per-segment frame counts over fps, matching the wire frames exactly', () => {
		// 2.5s and 3s at 16 fps go on the wire as 40 and 48 frames; the window is
		// 88/16, not (2.5 + 3).
		const chain = {
			fps: 16,
			segments: [chainSegment({ id: 'c1', duration: 2.5 }), chainSegment({ id: 'c2', duration: 3 })]
		};
		expect(chainKeyframeWindow(chain)).toBe(88 / 16);
	});

	it('rounding differences from the raw duration sum are real, not incidental', () => {
		const chain = { fps: 16, segments: [chainSegment({ id: 'c1', duration: 2.53 })] };
		expect(chainKeyframeWindow(chain)).toBe(Math.round(2.53 * 16) / 16);
		expect(chainKeyframeWindow(chain)).not.toBe(2.53);
	});

	it('returns 0 for an unusable fps rather than Infinity or NaN', () => {
		expect(chainKeyframeWindow({ fps: 0, segments: [chainSegment()] })).toBe(0);
		expect(chainKeyframeWindow({ fps: Number.NaN, segments: [chainSegment()] })).toBe(0);
	});
});

describe('createDefaultDirectorValue', () => {
	const caps = parseDirectorCapabilities(RAW_CAPS)!;

	it('picks the first enabled mode', () => {
		const v = createDefaultDirectorValue(caps);
		expect(v.mode).toBe('t2v');
	});

	it('uses capability durations/fps everywhere', () => {
		const v = createDefaultDirectorValue(caps);
		expect(v.simple.duration).toBe(5);
		expect(v.simple.fps).toBe(24);
		expect(v.timeline.duration).toBe(5);
		expect(v.timeline.fps).toBe(24);
		expect(v.chain.fps).toBe(24);
	});

	it('creates exactly one empty chain segment with a fixed (non-counter) id', () => {
		const v = createDefaultDirectorValue(caps);
		expect(v.chain.segments).toHaveLength(1);
		expect(v.chain.segments[0].id).toBe('chain-0');
		expect(v.chain.segments[0].prompt).toBe('');
		expect(v.chain.segments[0].loras).toBeNull();
		expect(v.chain.segments[0].keyframe).toBeNull();
		expect(v.chain.segments[0].sub_type_override).toBeNull();
	});

	it('seeds chain continuity defaults (falls back to overlap 4 / stitch on)', () => {
		expect(createDefaultDirectorValue(caps).chain.continuation).toEqual({ overlap_frames: 4, stitch: true });
		const wanCaps = parseDirectorCapabilities({
			...WAN_RAW_CAPS,
			modes: {
				...WAN_RAW_CAPS.modes,
				director: { ...WAN_RAW_CAPS.modes.director, continuation: { source: 'tail_frames', overlap_frames: 8, stitch: false } }
			}
		})!;
		expect(createDefaultDirectorValue(wanCaps).chain.continuation).toEqual({ overlap_frames: 8, stitch: false });
	});

	it('is deterministic: repeated independent calls produce byte-identical output', () => {
		// Regression test for an infinite-loop bug: VideoDirectorEditor.svelte's
		// re-sync $effect calls normalizeDirectorValue(value, capabilities) on
		// every reactive run, including runs triggered by its own writes. If
		// createDefaultDirectorValue (used internally as a fallback source) is
		// not a pure function of its inputs — e.g. it mints a fresh id from a
		// mutable module counter — two calls with the identical `value` prop
		// (which never changes on its own) produce different JSON, so the
		// JSON-equality re-sync check never converges: infinite effect loop,
		// hard browser hang. This asserts the invariant that must never regress.
		const a = createDefaultDirectorValue(caps);
		const b = createDefaultDirectorValue(caps);
		expect(JSON.stringify(a)).toBe(JSON.stringify(b));
	});

	it('starts with an empty timeline', () => {
		const v = createDefaultDirectorValue(caps);
		expect(v.timeline.segments).toEqual([]);
		expect(v.timeline.keyframes).toEqual([]);
		expect(v.timeline.audio).toEqual([]);
		expect(v.timeline.ic_lora).toEqual([]);
	});

	it('picks first enabled mode even when t2v is absent', () => {
		const c2 = parseDirectorCapabilities({ modes: { director: {} } })!;
		expect(createDefaultDirectorValue(c2).mode).toBe('director');
	});
});

// HYBRID_RAW_CAPS mirrors MiniMax-H3's real capability shape (segment_routing
// chain, keyframes anywhere, audio) -- the preset whose submission gate this
// function backs (generate/+page.svelte's videoDirectorShouldAttach).
describe('isDefaultDirectorDocument', () => {
	const caps = parseDirectorCapabilities(HYBRID_RAW_CAPS)!;

	it('is true for an untouched default (no stored value at all)', () => {
		expect(isDefaultDirectorDocument(undefined, caps)).toBe(true);
		expect(isDefaultDirectorDocument(null, caps)).toBe(true);
	});

	it('is true for a document freshly built by createDefaultDirectorValue', () => {
		expect(isDefaultDirectorDocument(createDefaultDirectorValue(caps), caps)).toBe(true);
	});

	it('is false once the default segment carries an edited prompt', () => {
		const def = createDefaultDirectorValue(caps);
		const editedSegment: ChainSegment = {
			...def.chain.segments[0],
			prompt: 'a cat walking',
			prompt_segments: [{ id: 'chain-0-prompt-0', content: 'a cat walking', chips: {}, type: 'content', enabled: true }]
		};
		const edited: VideoDirectorValue = { ...def, chain: { ...def.chain, segments: [editedSegment] } };
		expect(isDefaultDirectorDocument(edited, caps)).toBe(false);
	});

	it('is false once media is added to the default segment', () => {
		const def = createDefaultDirectorValue(caps);
		const withMedia: VideoDirectorValue = {
			...def,
			chain: { ...def.chain, segments: [{ ...def.chain.segments[0], keyframe: media('start.png') }] }
		};
		expect(isDefaultDirectorDocument(withMedia, caps)).toBe(false);
	});

	it('is false for a restored multi-segment document', () => {
		const def = createDefaultDirectorValue(caps);
		const restored: VideoDirectorValue = {
			...def,
			chain: {
				...def.chain,
				segments: [
					{ ...def.chain.segments[0], prompt: 'shot one' },
					{
						id: 'chain-1',
						prompt: 'shot two',
						prompt_segments: [],
						duration: 5,
						loras: null,
						keyframe: null,
						keyframe_strength: 1,
						last_keyframe: null,
						last_keyframe_strength: 1,
						sub_type_override: null
					}
				]
			}
		};
		expect(isDefaultDirectorDocument(restored, caps)).toBe(false);
	});

	it('ignores ui-only view state (zoom, panel collapse)', () => {
		const def = createDefaultDirectorValue(caps);
		const withUi: VideoDirectorValue = {
			...def,
			ui: { zoom: 2, collapsed: { inspector: true } }
		};
		expect(isDefaultDirectorDocument(withUi, caps)).toBe(true);
	});

	it('normalizes a stale/garbage stored value before comparing', () => {
		// A raw value shaped for a DIFFERENT preset's caps (extra unrecognized
		// mode, no chain.segments) re-shapes down to the same default via
		// normalizeDirectorValue, so it still reads as default.
		expect(isDefaultDirectorDocument({ mode: 'bogus_mode', chain: {} }, caps)).toBe(true);
	});
});

describe('normalizeDirectorValue', () => {
	const caps = parseDirectorCapabilities(RAW_CAPS)!;
	const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!;

	it('produces a full default value from garbage input', () => {
		const v = normalizeDirectorValue({ garbage: true }, caps);
		expect(v.schema_version).toBe(1);
		expect(v.mode).toBe('t2v');
		expect(v.global_prompt).toBe('');
		expect(v.chain.segments).toHaveLength(1);
	});

	it('produces a full default value from null/undefined', () => {
		expect(normalizeDirectorValue(null, caps).mode).toBe('t2v');
		expect(normalizeDirectorValue(undefined, caps).mode).toBe('t2v');
	});

	it('coerces an invalid mode to an enabled one', () => {
		const v = normalizeDirectorValue({ mode: 'nonexistent' }, caps);
		expect(v.mode).toBe('t2v');
	});

	it('preserves a valid mode', () => {
		const v = normalizeDirectorValue({ mode: 'director' }, caps);
		expect(v.mode).toBe('director');
	});

	it('leniently remaps the retired chain mode to director', () => {
		expect(normalizeDirectorValue({ mode: 'chain' }, caps).mode).toBe('director');
		expect(normalizeDirectorValue({ mode: 'chain' }, wanCaps).mode).toBe('director');
	});

	it('is idempotent: normalize(normalize(x)) deep-equals normalize(x)', () => {
		const inputs: unknown[] = [
			{},
			{ garbage: 1 },
			{ mode: 'director', timeline: { segments: [{ id: 's1', start: 0, end: 1, text: 'hi' }] } },
			{ mode: 'director', chain: { fps: 30, segments: [{ id: 'c1', prompt: 'a', duration: 3 }] } },
			{ mode: 'chain', chain: { segments: [] } },
			{ simple: { start_image: { path: '/x.png' } } }
		];
		for (const input of inputs) {
			const once = normalizeDirectorValue(input, caps);
			const twice = normalizeDirectorValue(once, caps);
			expect(twice).toEqual(once);
		}
	});

	it('coerces numeric fields and drops malformed array entries', () => {
		const v = normalizeDirectorValue(
			{
				timeline: {
					duration: 'not a number',
					segments: [{ id: 's1', start: 0, end: 1, text: 'ok' }, { start: 0, end: 1 }, 'nope'],
					keyframes: [{ id: 'k1', start: 0, role: 'first', strength: 1, media: { path: '/a.png' } }, { id: 'k2', role: 'bad' }]
				}
			},
			caps
		);
		expect(v.timeline.duration).toBe(5);
		expect(v.timeline.segments).toHaveLength(1);
		expect(v.timeline.keyframes).toHaveLength(1);
	});

	it('reads stored chain continuity, falling back to defaults', () => {
		const v = normalizeDirectorValue({ chain: { continuation: { overlap_frames: 12, stitch: false } } }, wanCaps);
		expect(v.chain.continuation).toEqual({ overlap_frames: 12, stitch: false });
		expect(normalizeDirectorValue({ chain: {} }, wanCaps).chain.continuation).toEqual({ overlap_frames: 4, stitch: true });
	});

	it('reads placed chain keyframes, coercing at/strength and dropping malformed entries', () => {
		const hybridCaps = parseDirectorCapabilities(HYBRID_RAW_CAPS)!;
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				chain: {
					segments: [{ id: 'c1', prompt: 'a' }],
					keyframes: [
						{ id: 'ckf-1', at: 2.5, strength: 0.6, media: { path: '/kf.png' } },
						{ id: 'ckf-2' },
						{ at: 1 },
						'nope'
					]
				}
			},
			hybridCaps
		);
		expect(v.chain.keyframes).toEqual([
			{ id: 'ckf-1', at: 2.5, strength: 0.6, media: { path: '/kf.png' } },
			{ id: 'ckf-2', at: 0, strength: 1, media: null }
		]);
	});

	it('gives every chain audio track an explicit role, defaulting an absent or bogus one to condition', () => {
		const hybridCaps = parseDirectorCapabilities(HYBRID_RAW_CAPS)!;
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				chain: {
					segments: [{ id: 'c1', prompt: 'a' }],
					audio: [
						{ id: 'a1', start: 0, trim_start: 0, length: 4, media: { path: '/a.wav' } },
						{ id: 'a2', role: 'mux', start: 1, trim_start: 0.5, length: 3, media: { path: '/b.wav' } },
						{ id: 'a3', role: 'sing', start: 0, trim_start: 0, length: 2, media: null }
					]
				}
			},
			hybridCaps
		);
		expect(v.chain.audio.map((a) => a.role)).toEqual(['condition', 'mux', 'condition']);
	});

	it('chain keyframes and audio default to empty arrays and survive re-normalization', () => {
		const hybridCaps = parseDirectorCapabilities(HYBRID_RAW_CAPS)!;
		const empty = normalizeDirectorValue({ mode: 'director' }, hybridCaps);
		expect(empty.chain.keyframes).toEqual([]);
		expect(empty.chain.audio).toEqual([]);

		const filled = normalizeDirectorValue(
			{
				mode: 'director',
				chain: {
					segments: [{ id: 'c1', prompt: 'a' }],
					keyframes: [{ id: 'ckf-1', at: 1, strength: 1, media: { path: '/kf.png' } }],
					audio: [{ id: 'a1', role: 'mux', start: 0, trim_start: 0, length: 4, media: { path: '/a.wav' } }]
				}
			},
			hybridCaps
		);
		expect(normalizeDirectorValue(filled, hybridCaps)).toEqual(filled);
	});

	it('regression: a timeline audio entry without a role never gains one', () => {
		// The LTX timeline editor shows no role control; its stored documents and
		// wire audio entries must keep normalizing exactly as they did before the
		// role existed (the backend reads an absent role as "condition").
		const v = normalizeDirectorValue(
			{ mode: 'director', timeline: { audio: [{ id: 'a1', start: 0, trim_start: 0, length: 4, media: { path: '/a.wav' } }] } },
			caps
		);
		expect(v.timeline.audio[0]).toEqual({ id: 'a1', start: 0, trim_start: 0, length: 4, media: { path: '/a.wav' } });
		expect('role' in v.timeline.audio[0]).toBe(false);
	});

	it('preserves a role a timeline audio entry already carries', () => {
		const v = normalizeDirectorValue(
			{ mode: 'director', timeline: { audio: [{ id: 'a1', role: 'mux', start: 0, trim_start: 0, length: 4, media: { path: '/a.wav' } }] } },
			caps
		);
		expect(v.timeline.audio[0].role).toBe('mux');
	});

	it('normalizes media refs, dropping ones without a path', () => {
		const v = normalizeDirectorValue({ simple: { start_image: { path: '/a.png' }, first_frame: { no_path: true } } }, caps);
		expect(v.simple.start_image).toEqual({ path: '/a.png' });
		expect(v.simple.first_frame).toBeNull();
	});

	it('migrates legacy prompt strings into deterministic editor segments', () => {
		const v = normalizeDirectorValue(
			{
				global_prompt: 'global legacy prompt',
				negative_prompt: 'negative legacy prompt',
				timeline: { segments: [{ id: 's1', start: 0, end: 2, text: 'timeline legacy prompt' }] },
				chain: { segments: [{ id: 'c1', prompt: 'chain legacy prompt', duration: 2 }] }
			},
			caps
		);

		expect(v.global_prompt_segments).toEqual([expect.objectContaining({ id: 'global-prompt-0', content: 'global legacy prompt' })]);
		expect(v.negative_prompt_segments).toEqual([expect.objectContaining({ id: 'negative-prompt-0', content: 'negative legacy prompt' })]);
		expect(v.timeline.segments[0].prompt_segments).toEqual([
			expect.objectContaining({ id: 's1-prompt-0', content: 'timeline legacy prompt' })
		]);
		expect(v.chain.segments[0].prompt_segments).toEqual([
			expect.objectContaining({ id: 'c1-prompt-0', content: 'chain legacy prompt' })
		]);
	});

	it('treats prompt segment collections as canonical and derives submission strings from them', () => {
		const v = normalizeDirectorValue(
			{
				global_prompt: 'stale global string',
				global_prompt_segments: [
					{ id: 'g1', content: 'first direction' },
					{ id: 'g2', content: 'disabled direction', isDisabled: true },
					{ id: 'g3', content: 'second direction' }
				],
				timeline: {
					segments: [
						{ id: 's1', start: 0, end: 2, text: 'stale timeline string', prompt_segments: [{ id: 't1', content: 'timed direction' }] }
					]
				},
				chain: {
					segments: [
						{ id: 'c1', prompt: 'stale chain string', prompt_segments: [{ id: 'c1p', content: 'shot direction' }], duration: 2 }
					]
				}
			},
			caps
		);

		expect(v.global_prompt).toBe('first direction, second direction');
		expect(v.timeline.segments[0].text).toBe('timed direction');
		expect(v.chain.segments[0].prompt).toBe('shot direction');
	});

	it('does not resurrect stale prompt strings after the last editor segment is removed', () => {
		const v = normalizeDirectorValue(
			{
				global_prompt: 'stale',
				global_prompt_segments: [],
				timeline: { segments: [{ id: 's1', start: 0, end: 2, text: 'stale', prompt_segments: [] }] },
				chain: { segments: [{ id: 'c1', prompt: 'stale', prompt_segments: [], duration: 2 }] }
			},
			caps
		);

		expect(v.global_prompt).toBe('');
		expect(v.timeline.segments[0].text).toBe('');
		expect(v.chain.segments[0].prompt).toBe('');
	});

	it('falls back to a fresh default chain segment when chain.segments is empty', () => {
		const v = normalizeDirectorValue({ chain: { segments: [] } }, caps);
		expect(v.chain.segments).toHaveLength(1);
		expect(v.chain.segments[0].id).toBe('chain-0');
	});

	it('parses a stored sub_type_override on a non-first segment', () => {
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				chain: { segments: [{ id: 'c1', prompt: 'a' }, { id: 'c2', prompt: 'b', sub_type_override: 't2v' }] }
			},
			wanCaps
		);
		expect(v.chain.segments[1].sub_type_override).toBe('t2v');
	});

	it('drops a stale sub_type_override on the first segment (never ambiguous)', () => {
		const v = normalizeDirectorValue(
			{ mode: 'director', chain: { segments: [{ id: 'c1', prompt: 'a', sub_type_override: 't2v' }] } },
			wanCaps
		);
		expect(v.chain.segments[0].sub_type_override).toBeNull();
	});

	it('drops a stale sub_type_override on a segment carrying a keyframe (index 0 with media)', () => {
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				chain: {
					segments: [{ id: 'c1', prompt: 'a', keyframe: media('/kf.png'), sub_type_override: 't2v' }]
				}
			},
			wanCaps
		);
		expect(v.chain.segments[0].sub_type_override).toBeNull();
	});

	it('ignores an unrecognized sub_type_override value', () => {
		const v = normalizeDirectorValue(
			{ mode: 'director', chain: { segments: [{ id: 'c1', prompt: 'a' }, { id: 'c2', prompt: 'b', sub_type_override: 'bogus' }] } },
			wanCaps
		);
		expect(v.chain.segments[1].sub_type_override).toBeNull();
	});

	it('is deterministic across independent calls on the same raw input (not just nested re-normalization)', () => {
		// The stronger invariant VideoDirectorEditor.svelte's re-sync $effect
		// actually depends on: it re-derives `next = normalizeDirectorValue(value,
		// capabilities)` from the ORIGINAL `value` prop on every reactive run —
		// not from its own previous output — so `value` staying constant (e.g.
		// undefined, on a brand-new tab) must always normalize to the same JSON,
		// or the JSON-equality re-sync check never converges (infinite loop).
		const rawInputs: unknown[] = [undefined, null, {}, { mode: 'director' }, { chain: { segments: [] } }];
		for (const raw of rawInputs) {
			const a = normalizeDirectorValue(raw, caps);
			const b = normalizeDirectorValue(raw, caps);
			expect(JSON.stringify(b)).toBe(JSON.stringify(a));
		}
	});

	it('is invariant to the capabilities object identity — only its content matters', () => {
		// +page.svelte memoizes `videoDirectorCaps` on content, not identity, but
		// normalizeDirectorValue must not rely on that: a structurally-identical
		// but freshly-parsed capabilities object must still normalize the same
		// raw value to byte-identical JSON.
		const capsCopy = parseDirectorCapabilities(RAW_CAPS)!;
		expect(capsCopy).not.toBe(caps);
		const raw = { mode: 'director', chain: { segments: [] } };
		expect(JSON.stringify(normalizeDirectorValue(raw, caps))).toBe(JSON.stringify(normalizeDirectorValue(raw, capsCopy)));
	});

	it('preserves ui state when it is an object', () => {
		const v = normalizeDirectorValue({ ui: { zoom: 120 } }, caps);
		expect(v.ui).toEqual({ zoom: 120 });
	});

	it('omits ui when absent or malformed', () => {
		expect(normalizeDirectorValue({}, caps).ui).toBeUndefined();
		expect(normalizeDirectorValue({ ui: 'nope' }, caps).ui).toBeUndefined();
	});
});

describe('validateDirector', () => {
	const caps = parseDirectorCapabilities(RAW_CAPS)!;
	const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!;

	it('t2v requires a non-empty global prompt', () => {
		const v = normalizeDirectorValue({ mode: 't2v' }, caps);
		expect(validateDirector(v, caps)).toEqual({ ok: false, reasons: ['Missing prompt'] });
		const v2 = { ...v, global_prompt: 'a dog running' };
		expect(validateDirector(v2, caps).ok).toBe(true);
	});

	it('i2v requires a start image and a prompt -- derived, so an empty document reads as t2v instead', () => {
		// Under the modeless redesign there is no "picked i2v but no image yet"
		// state to validate: a shot with no edge media IS t2v (deriveDirectorMode),
		// so "Missing start image" only ever fires once media makes the document
		// actually i2v-shaped -- at which point it's satisfied by construction.
		const v = normalizeDirectorValue({ mode: 'i2v' }, caps);
		expect(validateDirector(v, caps)).toEqual({ ok: false, reasons: ['Missing prompt'] });

		const withImage = { ...v, simple: { ...v.simple, start_image: media('/a.png') } };
		expect(validateDirector(withImage, caps)).toEqual({ ok: false, reasons: ['Missing prompt'] });

		const v2 = { ...withImage, global_prompt: 'p' };
		expect(validateDirector(v2, caps).ok).toBe(true);
	});

	it('flf requires both first and last frame plus a prompt -- only reachable once both edges are set', () => {
		// Same reasoning as i2v above: a single-edge document derives to i2v, not
		// a "flf missing its other edge" -- flf is defined BY having both edges,
		// so "Missing first/last frame" is dead code kept for defense in depth
		// (e.g. a caller that constructs a value directly, bypassing derivation).
		const v = normalizeDirectorValue({ mode: 'flf' }, caps);
		expect(validateDirector(v, caps)).toEqual({ ok: false, reasons: ['Missing prompt'] });

		const withFirst = { ...v, simple: { ...v.simple, first_frame: media('/a.png') } };
		expect(validateDirector(withFirst, caps)).toEqual({ ok: false, reasons: ['Missing prompt'] });

		const withBoth = { ...withFirst, global_prompt: 'p', simple: { ...withFirst.simple, last_frame: media('/b.png') } };
		expect(validateDirector(withBoth, caps).ok).toBe(true);
	});

	it('timeline director requires global prompt or a segment with text', () => {
		const v = normalizeDirectorValue({ mode: 'director' }, caps);
		expect(validateDirector(v, caps).reasons).toContain('Missing prompt');

		const withSeg = {
			...v,
			timeline: { ...v.timeline, segments: [{ id: 's1', start: 0, end: 1, text: 'hello', prompt_segments: [] }] }
		};
		expect(validateDirector(withSeg, caps).ok).toBe(true);
	});

	it('timeline director flags keyframes/audio/ic_lora entries missing media', () => {
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				global_prompt: 'p',
				timeline: {
					keyframes: [{ id: 'k1', start: 0, role: 'first', strength: 1, media: null }],
					audio: [{ id: 'a1', start: 0, trim_start: 0, length: 1, media: null }],
					ic_lora: [{ id: 'i1', lora: null, ref_media: null, strength: 1 }]
				}
			},
			caps
		);
		const result = validateDirector(v, caps);
		expect(result.reasons).toContain('Keyframe missing media');
		expect(result.reasons).toContain('Audio segment missing media');
		expect(result.reasons).toContain('IC-LoRA entry missing a LoRA');
	});

	it('timeline director enforces maxDuration and maxKeyframes', () => {
		const v = normalizeDirectorValue({ mode: 'director', global_prompt: 'p', timeline: { duration: 100 } }, caps);
		expect(validateDirector(v, caps).reasons).toContain('Duration exceeds maximum of 30s');

		const manyKeyframes = Array.from({ length: 9 }, (_, i) => ({
			id: `k${i}`,
			start: i,
			role: 'free' as const,
			strength: 1,
			media: media('/a.png')
		}));
		const v2 = normalizeDirectorValue(
			{ mode: 'director', global_prompt: 'p', timeline: { duration: 5, keyframes: manyKeyframes } },
			caps
		);
		expect(validateDirector(v2, caps).reasons).toContain('Too many keyframes (max 8)');
	});

	it('timeline director mirrors the edge/free-placement allowances for a stale document', () => {
		// No `director` capability declared at all (only t2v/i2v) -- i2v opens the
		// leading edge, but there is no trailing edge and no free placement.
		const restrictedCaps = parseDirectorCapabilities({ modes: { t2v: {}, i2v: {} } })!;
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				global_prompt: 'p',
				timeline: {
					duration: 5,
					segments: [{ id: 's1', start: 0, end: 5, text: 'a' }],
					keyframes: [
						{ id: 'k1', start: 0, role: 'first', strength: 1, media: media('/a.png') },
						{ id: 'k2', start: 5, role: 'last', strength: 1, media: media('/b.png') },
						{ id: 'k3', start: 2, role: 'free', strength: 1, media: media('/c.png') }
					]
				}
			},
			restrictedCaps
		);
		const result = validateDirector(v, restrictedCaps);
		expect(result.reasons).not.toContain('This mode has no start-frame slot');
		expect(result.reasons).toContain('This mode has no end-frame slot');
		expect(result.reasons).toContain('Free keyframe placement is not supported in this mode');
	});

	it('uses the shared timing validation for simple, timeline, and routed-chain modes', () => {
		const cappedCaps = parseDirectorCapabilities({
			modes: { t2v: {}, director: {} },
			limits: { default_duration: 5, default_fps: 25, max_frames: 124, max_duration: 10 }
		})!;
		const simple = normalizeDirectorValue(
			{ mode: 't2v', global_prompt: 'p', simple: { duration: 5, fps: 25 } },
			cappedCaps
		);
		expect(validateDirector(simple, cappedCaps).reasons.join(' ')).toContain('generator cap of 124 frames');

		const timeline = normalizeDirectorValue(
			{ mode: 'director', global_prompt: 'p', timeline: { duration: 5, fps: 25 } },
			cappedCaps
		);
		expect(validateDirector(timeline, cappedCaps).reasons.join(' ')).toContain('generator cap of 124 frames');

		const chainCaps = parseDirectorCapabilities({
			segment_routing: true,
			modes: { director: {} },
			limits: { max_frames: 1 }
		})!;
		const chain = normalizeDirectorValue(
			{ mode: 'director', chain: { fps: 61, segments: [{ id: 'c1', prompt: 'a shot' }] } },
			chainCaps
		);
		expect(validateDirector(chain, chainCaps).reasons).toContain('FPS must be between 1 and 60, got 61.');
	});

	it('routed-chain director requires every segment to have a non-empty prompt', () => {
		// A single segment is deriveDirectorMode's t2v/i2v/flf territory (see the
		// tests above), so this needs a second shot to stay director-shaped.
		const v = normalizeDirectorValue(
			{ mode: 'director', chain: { segments: [{ id: 'c1', prompt: 'a shot' }, { id: 'c2', prompt: '' }] } },
			wanCaps
		);
		expect(validateDirector(v, wanCaps).reasons).toContain('Every segment needs a prompt');

		const v2 = normalizeDirectorValue(
			{ mode: 'director', chain: { segments: [{ id: 'c1', prompt: 'a shot' }, { id: 'c2', prompt: 'b shot' }] } },
			wanCaps
		);
		expect(validateDirector(v2, wanCaps).ok).toBe(true);
	});

	it('routed-chain director enforces maxSegments', () => {
		const segs = Array.from({ length: 9 }, (_, i) => ({ id: `c${i}`, prompt: 'x' }));
		const v = normalizeDirectorValue({ mode: 'director', chain: { segments: segs } }, wanCaps);
		expect(validateDirector(v, wanCaps).reasons).toContain('Too many segments (max 8)');
	});

	it('routed-chain director: a non-first segment carrying its own keyframe is join-aware valid, not index-pinned', () => {
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				chain: {
					segments: [
						{ id: 'c1', prompt: 'a', keyframe: media('/a.png') },
						{ id: 'c2', prompt: 'b', keyframe: media('/b.png') }
					]
				}
			},
			wanCaps
		);
		// c2's own keyframe makes it resolve to 'i2v' -- a fresh open, not a
		// continuation -- so keyframes=first_only ("no free-floating timeline")
		// has nothing to object to here.
		expect(validateDirector(v, wanCaps).reasons).not.toContain('Only the first segment may have a keyframe');
	});

	it('routed-chain director: a trailing frame with no leading frame on the same segment is rejected', () => {
		const v = normalizeDirectorValue(
			{ mode: 'director', chain: { segments: [{ id: 'c1', prompt: 'a', last_keyframe: media('/z.png') }] } },
			wanCaps
		);
		expect(validateDirector(v, wanCaps).reasons).toContain('A trailing frame needs a leading frame on the same segment to have any effect');
	});

	describe('hybrid chain director (keyframes anywhere + audio + overlap cap)', () => {
		const hybridCaps = parseDirectorCapabilities(HYBRID_RAW_CAPS)!;

		function hybridDoc(chain: Record<string, unknown>) {
			return normalizeDirectorValue(
				{ mode: 'director', chain: { fps: 16, segments: [{ id: 'c1', prompt: 'a shot', duration: 4 }], ...chain } },
				hybridCaps
			);
		}

		// A lone segment with no media/keyframes/audio is deriveDirectorMode's
		// t2v/i2v/flf territory (see the validateDirector tests above); this
		// variant adds a second shot so a chain-wide-only fixture (continuation)
		// stays genuinely director-shaped without disturbing hybridDoc's window
		// math for the tests above that already attach media/keyframes/audio.
		function hybridChainDoc(chain: Record<string, unknown>) {
			return normalizeDirectorValue(
				{
					mode: 'director',
					chain: {
						fps: 16,
						segments: [{ id: 'c1', prompt: 'a shot', duration: 4 }, { id: 'c2', prompt: 'b shot', duration: 4 }],
						...chain
					}
				},
				hybridCaps
			);
		}

		it('accepts a keyframe placed inside the chain window and rejects one outside it', () => {
			const inside = hybridDoc({ keyframes: [{ id: 'k1', at: 2, strength: 1, media: media('/kf.png') }] });
			expect(validateDirector(inside, hybridCaps).ok).toBe(true);

			const outside = hybridDoc({ keyframes: [{ id: 'k1', at: 9, strength: 1, media: media('/kf.png') }] });
			expect(validateDirector(outside, hybridCaps).reasons).toContain('Every keyframe must sit between 0s and 4.00s');
		});

		it('flags a keyframe with no media and enforces max_keyframes', () => {
			const noMedia = hybridDoc({ keyframes: [{ id: 'k1', at: 1, strength: 1, media: null }] });
			expect(validateDirector(noMedia, hybridCaps).reasons).toContain('Keyframe missing media');

			const tooMany = hybridDoc({
				keyframes: Array.from({ length: 5 }, (_, i) => ({ id: `k${i}`, at: 1, strength: 1, media: media('/kf.png') }))
			});
			expect(validateDirector(tooMany, hybridCaps).reasons).toContain('Too many keyframes (max 4)');
		});

		it('falls back to the backend default of 8 keyframes when the mode declares no max', () => {
			const noMaxCaps = parseDirectorCapabilities({
				segment_routing: true,
				modes: { director: { keyframes: 'anywhere' } },
				limits: { default_fps: 16 }
			})!;
			const v = normalizeDirectorValue(
				{
					mode: 'director',
					chain: {
						fps: 16,
						segments: [{ id: 'c1', prompt: 'a shot', duration: 10 }],
						keyframes: Array.from({ length: 9 }, (_, i) => ({ id: `k${i}`, at: 1, strength: 1, media: { path: '/kf.png' } }))
					}
				},
				noMaxCaps
			);
			expect(validateDirector(v, noMaxCaps).reasons).toContain('Too many keyframes (max 8)');
		});

		it('rejects placed keyframes and audio a first_only/no-audio mode cannot honour', () => {
			const wanCapsLocal = parseDirectorCapabilities(WAN_RAW_CAPS)!;
			const stale = normalizeDirectorValue(
				{
					mode: 'director',
					chain: {
						segments: [{ id: 'c1', prompt: 'a shot' }],
						keyframes: [{ id: 'k1', at: 1, strength: 1, media: { path: '/kf.png' } }],
						audio: [{ id: 'a1', start: 0, trim_start: 0, length: 2, media: { path: '/a.wav' } }]
					}
				},
				wanCapsLocal
			);
			const reasons = validateDirector(stale, wanCapsLocal).reasons;
			expect(reasons).toContain('Timed keyframes are not supported in this mode');
			expect(reasons).toContain('Audio is not supported in this mode');
		});

		it('flags an audio track with no media', () => {
			const v = hybridDoc({ audio: [{ id: 'a1', start: 0, trim_start: 0, length: 2, media: null }] });
			expect(validateDirector(v, hybridCaps).reasons).toContain('Audio track missing media');
		});

		it('rejects a continuation overlap above the mode max_overlap_frames', () => {
			const over = hybridChainDoc({ continuation: { overlap_frames: 7, stitch: true } });
			expect(validateDirector(over, hybridCaps).reasons).toContain("Overlap frames exceed this mode's maximum of 6");

			const atMax = hybridChainDoc({ continuation: { overlap_frames: 6, stitch: true } });
			expect(validateDirector(atMax, hybridCaps).ok).toBe(true);
		});

		it('leaves a mode declaring no max_overlap_frames unbounded', () => {
			const wanCapsLocal = parseDirectorCapabilities(WAN_RAW_CAPS)!;
			const v = normalizeDirectorValue(
				{
					mode: 'director',
					chain: {
						segments: [{ id: 'c1', prompt: 'a' }, { id: 'c2', prompt: 'b' }],
						continuation: { overlap_frames: 99, stitch: true }
					}
				},
				wanCapsLocal
			);
			expect(validateDirector(v, wanCapsLocal).ok).toBe(true);
		});

		it('still allows the opening shot its start image', () => {
			const v = hybridDoc({ segments: [{ id: 'c1', prompt: 'a shot', duration: 4, keyframe: { path: '/start.png' } }] });
			expect(validateDirector(v, hybridCaps).ok).toBe(true);
		});
	});

	it('routed-chain director allows per-segment loras only when the capability is enabled', () => {
		// Two segments so the fixture stays director-shaped -- a lone segment
		// derives to t2v/i2v/flf (see the validateDirector tests above), which
		// never reaches this chain-only check at all.
		const noPerSeg = parseDirectorCapabilities({
			segment_routing: true,
			modes: { director: { per_segment_loras: false } }
		})!;
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				chain: { segments: [{ id: 'c1', prompt: 'a', loras: { high: [], low: [] } }, { id: 'c2', prompt: 'b' }] }
			},
			noPerSeg
		);
		expect(validateDirector(v, noPerSeg).reasons).toContain('Per-segment LoRAs are not supported in this mode');

		const v2 = normalizeDirectorValue(
			{
				mode: 'director',
				chain: { segments: [{ id: 'c1', prompt: 'a', loras: { high: [], low: [] } }, { id: 'c2', prompt: 'b' }] }
			},
			wanCaps
		);
		expect(validateDirector(v2, wanCaps).ok).toBe(true);
	});
});

describe('buildDirectorSubmission', () => {
	const caps = parseDirectorCapabilities(RAW_CAPS)!;
	const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!;

	function base(mode: VideoDirectorValue['mode'], c = caps): VideoDirectorValue {
		return normalizeDirectorValue({ mode, ui: { zoom: 99 } }, c);
	}

	it('t2v: settings from simple, one segment with global prompt, no media', () => {
		const v = { ...base('t2v'), global_prompt: 'a red car' };
		const wire = buildDirectorSubmission(v, caps);
		expect(wire.schema_version).toBe(1);
		expect(wire.mode).toBe('t2v');
		expect(wire.settings).toEqual({ fps: 24, duration: 5, seed: -1 });
		expect(wire.segments).toEqual([
			{
				id: 'seg-1',
				prompt: 'a red car',
				negative_prompt: '',
				start: 0,
				end: 5,
				frames: null,
				seed: null,
				steps: null,
				cfg: null,
				loras: null
			}
		]);
		expect(wire.media).toEqual([]);
		expect(wire.audio).toEqual([]);
		expect(wire.ic_lora).toEqual([]);
		expect((wire as unknown as { ui?: unknown }).ui).toBeUndefined();
	});

	it('i2v: adds a first-role media entry for the start image', () => {
		const v = { ...base('i2v'), global_prompt: 'p', simple: { ...base('i2v').simple, start_image: media('/s.png') } };
		const wire = buildDirectorSubmission(v, caps);
		expect(wire.media).toEqual([{ id: 'm-1', role: 'first', segment_id: 'seg-1', at: 0, strength: 1, media: media('/s.png') }]);
	});

	it('flf: adds first and last media entries, last at duration', () => {
		const b = base('flf');
		const v = {
			...b,
			global_prompt: 'p',
			simple: { ...b.simple, duration: 7, first_frame: media('/f.png'), last_frame: media('/l.png') }
		};
		const wire = buildDirectorSubmission(v, caps);
		expect(wire.media).toEqual([
			{ id: 'm-1', role: 'first', segment_id: 'seg-1', at: 0, strength: 1, media: media('/f.png') },
			{ id: 'm-2', role: 'last', segment_id: 'seg-1', at: 7, strength: 1, media: media('/l.png') }
		]);
	});

	it('timeline director: maps sorted segments, synthesizes one when empty, and keyframes/audio/ic_lora pass through', () => {
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				global_prompt: 'anchor',
				timeline: {
					duration: 10,
					segments: [
						{ id: 's2', start: 5, end: 10, text: 'second' },
						{ id: 's1', start: 0, end: 5, text: 'first' }
					],
					keyframes: [
						{ id: 'k1', start: 0, role: 'first', strength: 1, media: media('/kf.png') },
						{ id: 'k2', start: 10, role: 'last', strength: 0.8, media: media('/kl.png') },
						{ id: 'k3', start: 3, role: 'free', strength: 0.5, media: media('/km.png') }
					],
					audio: [{ id: 'a1', start: 0, trim_start: 0.5, length: 4, media: media('/aud.mp3') }],
					ic_lora: [{ id: 'i1', lora: { model: 'style.safetensors', strength: 0.9 }, ref_media: media('/ref.png'), strength: 0.9 }]
				}
			},
			caps
		);
		const wire = buildDirectorSubmission(v, caps);
		expect(wire.mode).toBe('director');
		expect(wire.segments.map((s) => s.id)).toEqual(['s1', 's2']);
		// The global prompt is prefixed onto the FIRST segment only — the LTX
		// pipeline joins all director wire segments into one prompt, so
		// prefixing every segment (chain's rule) would repeat it N times.
		expect(wire.segments[0].prompt).toBe('anchor. first');
		expect(wire.segments[1].prompt).toBe('second');
		expect(wire.media).toEqual([
			{ id: 'm-1', role: 'first', segment_id: 's1', at: 0, strength: 1, media: media('/kf.png') },
			{ id: 'm-2', role: 'last', segment_id: 's2', at: 10, strength: 0.8, media: media('/kl.png') },
			{ id: 'm-3', role: 'keyframe', segment_id: null, at: 3, strength: 0.5, media: media('/km.png') }
		]);
		expect(wire.audio).toEqual([{ id: 'a1', start: 0, trim_start: 0.5, length: 4, media: media('/aud.mp3') }]);
		expect(wire.ic_lora).toEqual([
			{ id: 'i1', lora: { model: 'style.safetensors', strength: 0.9 }, reference: media('/ref.png'), strength: 0.9 }
		]);
	});

	it('preserves a toggled-off ic_lora saved_strength through normalize and submission (reload persistence)', () => {
		// normalizeDirectorValue runs on every reactive re-sync tick
		// (VideoDirectorEditor.svelte) - it must not wipe the toggle-off
		// memory a LoraPickerField row carries on the wire value.
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				global_prompt: 'anchor',
				timeline: {
					duration: 10,
					ic_lora: [
						{ id: 'i1', lora: { model: 'style.safetensors', strength: 0, saved_strength: 0.9 }, ref_media: null, strength: 1 }
					]
				}
			},
			caps
		);
		expect(v.timeline.ic_lora[0].lora).toEqual({ model: 'style.safetensors', strength: 0, saved_strength: 0.9 });

		// Re-normalizing an already-normalized value must be idempotent (the
		// re-sync effect's convergence check relies on this).
		const again = normalizeDirectorValue(v, caps);
		expect(again.timeline.ic_lora[0].lora).toEqual(v.timeline.ic_lora[0].lora);

		// The backend's own normalizer (src/features/video_director/normalize.py)
		// is what actually drops saved_strength at submission time - this wire
		// builder passes it through untouched, which is fine either way.
		const wire = buildDirectorSubmission(v, caps);
		expect(wire.ic_lora[0].lora).toEqual({ model: 'style.safetensors', strength: 0, saved_strength: 0.9 });
	});

	it('timeline director: carries an ic_lora reference MediaRef type through to the wire document', () => {
		// The backend routes a video-typed ic_lora reference into media_videos
		// and an image-typed one into media_images, keyed off `type` alone
		// (src/features/video_director/normalize.py::_media_ref_is_image treats
		// an absent `type` as an image) - so normalize/submission must not drop
		// it, or a reference clip reaches the still-image loader.
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				global_prompt: 'anchor',
				timeline: {
					duration: 10,
					ic_lora: [
						{ id: 'i1', lora: { model: 'style.safetensors', strength: 1 }, ref_media: { path: '/clip.mp4', type: 'video' }, strength: 0.7 },
						{ id: 'i2', lora: { model: 'other.safetensors', strength: 1 }, ref_media: { path: '/still.png', type: 'image' }, strength: 0.5 }
					]
				}
			},
			caps
		);
		expect(v.timeline.ic_lora.map((e) => (e.ref_media as MediaRef | null)?.type)).toEqual(['video', 'image']);

		const wire = buildDirectorSubmission(v, caps);
		expect(wire.ic_lora.map((e) => e.reference)).toEqual([
			{ path: '/clip.mp4', type: 'video' },
			{ path: '/still.png', type: 'image' }
		]);
	});

	it('timeline director: synthesizes a single full-range segment from global_prompt when no timeline segments', () => {
		const v = normalizeDirectorValue({ mode: 'director', global_prompt: 'only global', timeline: { duration: 6 } }, caps);
		const wire = buildDirectorSubmission(v, caps);
		expect(wire.segments).toEqual([
			{
				id: 'seg-1',
				prompt: 'only global',
				negative_prompt: '',
				start: 0,
				end: 6,
				frames: null,
				seed: null,
				steps: null,
				cfg: null,
				loras: null
			}
		]);
	});

	it('timeline director: segment-only text (no global prompt) is used as-is', () => {
		const v = normalizeDirectorValue(
			{ mode: 'director', global_prompt: '', timeline: { duration: 5, segments: [{ id: 's1', start: 0, end: 5, text: 'solo text' }] } },
			caps
		);
		const wire = buildDirectorSubmission(v, caps);
		expect(wire.segments[0].prompt).toBe('solo text');
	});

	it('timeline director: the global prompt appears exactly once across all wire segment prompts (LTX joins them into one prompt)', () => {
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				global_prompt: 'anchor',
				timeline: {
					duration: 15,
					segments: [
						{ id: 's1', start: 0, end: 5, text: 'first' },
						{ id: 's2', start: 5, end: 10, text: 'second' },
						{ id: 's3', start: 10, end: 15, text: 'third' }
					]
				}
			},
			caps
		);
		const wire = buildDirectorSubmission(v, caps);
		const joined = wire.segments.map((s) => s.prompt).join(' ');
		expect(joined.match(/anchor/g)).toHaveLength(1);
		expect(wire.segments.map((s) => s.prompt)).toEqual(['anchor. first', 'second', 'third']);
	});

	it('routed-chain director: emits mode "director", sums durations, rounds frames, keeps per-segment loras', () => {
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				global_prompt: 'anchor',
				chain: {
					fps: 16,
					segments: [
						{ id: 'c1', prompt: 'shot one', duration: 2.5, loras: { high: [{ model: 'a', strength: 1 }], low: [] } },
						{ id: 'c2', prompt: 'shot two', duration: 3 }
					]
				}
			},
			wanCaps
		);
		const wire = buildDirectorSubmission(v, wanCaps);
		expect(wire.mode).toBe('director');
		expect(wire.settings).toEqual({
			fps: 16,
			duration: 5.5,
			seed: -1,
			continuation: { source: 'tail_frames', overlap_frames: 4, stitch: true }
		});
		expect(wire.segments[0]).toMatchObject({
			id: 'c1',
			prompt: 'anchor. shot one',
			frames: Math.round(2.5 * 16),
			start: null,
			end: null,
			loras: { high: [{ model: 'a', strength: 1 }], low: [] }
		});
		expect(wire.segments[1]).toMatchObject({ id: 'c2', prompt: 'anchor. shot two', frames: Math.round(3 * 16), loras: null });
	});

	it('preserves a toggled-off per-segment LoRA saved_strength through normalize (reload persistence)', () => {
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				global_prompt: 'anchor',
				chain: {
					fps: 16,
					segments: [
						{
							id: 'c1',
							prompt: 'shot one',
							duration: 2.5,
							loras: { high: [{ model: 'a', strength: 0, saved_strength: 1.4 }], low: [{ model: 'b', strength: 0.6 }] }
						}
					]
				}
			},
			wanCaps
		);
		expect(v.chain.segments[0].loras).toEqual({
			high: [{ model: 'a', strength: 0, saved_strength: 1.4 }],
			low: [{ model: 'b', strength: 0.6 }]
		});

		// Idempotent under repeated re-normalization, same as the ic_lora case.
		const again = normalizeDirectorValue(v, wanCaps);
		expect(again.chain.segments[0].loras).toEqual(v.chain.segments[0].loras);
	});

	it('routed-chain director: continuity settings flow from the value into the wire doc', () => {
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				chain: { fps: 16, segments: [{ id: 'c1', prompt: 'a' }, { id: 'c2', prompt: 'b' }], continuation: { overlap_frames: 9, stitch: false } }
			},
			wanCaps
		);
		const wire = buildDirectorSubmission(v, wanCaps);
		expect(wire.settings.continuation).toEqual({ source: 'tail_frames', overlap_frames: 9, stitch: false });
	});

	it('routed-chain director: every segment\'s own keyframe becomes wire media, not just segment 0', () => {
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				chain: {
					segments: [
						{ id: 'c1', prompt: 'a', keyframe: media('/kf.png'), keyframe_strength: 0.7 },
						{ id: 'c2', prompt: 'b', keyframe: media('/other.png') }
					]
				}
			},
			wanCaps
		);
		const wire = buildDirectorSubmission(v, wanCaps);
		expect(wire.media).toEqual([
			{ id: 'm-1', role: 'first', segment_id: 'c1', at: 0, strength: 0.7, media: media('/kf.png') },
			{ id: 'm-2', role: 'first', segment_id: 'c2', at: 0, strength: 1, media: media('/other.png') }
		]);
	});

	it('routed-chain director: a segment\'s own trailing frame becomes wire media role "last", paired with its leading one', () => {
		// Two segments (rather than one) so the document stays `director` --
		// a single flf-paired shot alone would derive to the legacy `flf` mode
		// (deriveDirectorMode) and take a different wire branch entirely.
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				chain: {
					segments: [
						{
							id: 'c1',
							prompt: 'a',
							keyframe: media('/kf.png'),
							last_keyframe: media('/end.png'),
							last_keyframe_strength: 0.5,
							duration: 3
						},
						{ id: 'c2', prompt: 'b' }
					]
				}
			},
			wanCaps
		);
		expect(v.mode).toBe('director');
		const wire = buildDirectorSubmission(v, wanCaps);
		expect(wire.mode).toBe('director');
		expect(wire.media).toEqual([
			{ id: 'm-1', role: 'first', segment_id: 'c1', at: 0, strength: 1, media: media('/kf.png') },
			{ id: 'm-2', role: 'last', segment_id: 'c1', at: 3, strength: 0.5, media: media('/end.png') }
		]);
	});

	it('routed-chain director: never emits sub_type when no segment has an override (absent, not the derived value)', () => {
		const v = normalizeDirectorValue(
			{ mode: 'director', chain: { segments: [{ id: 'c1', prompt: 'a' }, { id: 'c2', prompt: 'b' }] } },
			wanCaps
		);
		const wire = buildDirectorSubmission(v, wanCaps);
		expect(wire.segments.every((s) => !('sub_type' in s))).toBe(true);
	});

	it('routed-chain director: emits sub_type only on the segment carrying an explicit override', () => {
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				chain: {
					segments: [
						{ id: 'c1', prompt: 'a' },
						{ id: 'c2', prompt: 'b', sub_type_override: 't2v' },
						{ id: 'c3', prompt: 'c' }
					]
				}
			},
			wanCaps
		);
		const wire = buildDirectorSubmission(v, wanCaps);
		expect(wire.segments[0]).not.toHaveProperty('sub_type');
		expect(wire.segments[1]).toMatchObject({ sub_type: 't2v' });
		expect(wire.segments[2]).not.toHaveProperty('sub_type');
	});

	it('routed-chain director: drops a stale override instead of sending a conflicting sub_type (segment not ambiguous)', () => {
		// c1 carries both a keyframe (forces i2v) AND a leftover override --
		// normalizeDirectorValue already strips this, but buildDirectorSubmission
		// re-checks ambiguity defensively rather than trusting its input blindly.
		const v = normalizeDirectorValue({ mode: 'director', chain: { segments: [{ id: 'c1', prompt: 'a' }] } }, wanCaps);
		v.chain.segments[0] = { ...v.chain.segments[0], keyframe: media('/kf.png'), sub_type_override: 't2v' };
		const wire = buildDirectorSubmission(v, wanCaps);
		expect(wire.segments[0]).not.toHaveProperty('sub_type');
	});

	describe('hybrid chain director wire shape', () => {
		const hybridCaps = parseDirectorCapabilities(HYBRID_RAW_CAPS)!;

		it('emits placed keyframes as role "keyframe" media and audio tracks carrying a role', () => {
			const v = normalizeDirectorValue(
				{
					mode: 'director',
					global_prompt: 'anchor',
					chain: {
						fps: 16,
						segments: [
							{ id: 'c1', prompt: 'shot one', duration: 2, keyframe: { path: '/start.png' }, keyframe_strength: 0.9 },
							{ id: 'c2', prompt: 'shot two', duration: 2 }
						],
						continuation: { overlap_frames: 6, stitch: true },
						keyframes: [
							{ id: 'ckf-1', at: 1.5, strength: 0.7, media: { path: '/mid.png' } },
							{ id: 'ckf-2', at: 3, strength: 1, media: { path: '/late.png' } },
							{ id: 'ckf-3', at: 3.5, strength: 1, media: null }
						],
						audio: [
							{ id: 'caud-1', role: 'mux', start: 0, trim_start: 0.25, length: 4, media: { path: '/track.wav' } }
						]
					}
				},
				hybridCaps
			);
			const wire = buildDirectorSubmission(v, hybridCaps);

			// The opening start image keeps role "first"; every placed keyframe is a
			// segment-less "keyframe" entry positioned by `at`, numbered after it.
			expect(wire.media).toEqual([
				{ id: 'm-1', role: 'first', segment_id: 'c1', at: 0, strength: 0.9, media: media('/start.png') },
				{ id: 'm-2', role: 'keyframe', segment_id: null, at: 1.5, strength: 0.7, media: media('/mid.png') },
				{ id: 'm-3', role: 'keyframe', segment_id: null, at: 3, strength: 1, media: media('/late.png') }
			]);
			expect(wire.audio).toEqual([
				{ id: 'caud-1', role: 'mux', start: 0, trim_start: 0.25, length: 4, media: media('/track.wav') }
			]);
			expect(wire.settings.continuation).toEqual({ source: 'tail_frames', overlap_frames: 6, stitch: true });
		});

		it('defaults an audio track with no stored role to "condition" on the wire', () => {
			const v = normalizeDirectorValue(
				{
					mode: 'director',
					chain: {
						fps: 16,
						segments: [{ id: 'c1', prompt: 'a shot', duration: 2 }],
						audio: [{ id: 'caud-1', start: 0, trim_start: 0, length: 2, media: { path: '/track.wav' } }]
					}
				},
				hybridCaps
			);
			expect(buildDirectorSubmission(v, hybridCaps).audio[0].role).toBe('condition');
		});

		it('emits nothing for unused keyframe/audio sections', () => {
			const v = normalizeDirectorValue(
				{ mode: 'director', chain: { fps: 16, segments: [{ id: 'c1', prompt: 'a shot', duration: 2 }] } },
				hybridCaps
			);
			const wire = buildDirectorSubmission(v, hybridCaps);
			expect(wire.media).toEqual([]);
			expect(wire.audio).toEqual([]);
		});

		it('drops keyframes and audio a mode no longer declares rather than sending a rejected document', () => {
			const wanCapsLocal = parseDirectorCapabilities(WAN_RAW_CAPS)!;
			const stale = normalizeDirectorValue(
				{
					mode: 'director',
					chain: {
						segments: [{ id: 'c1', prompt: 'a shot' }],
						keyframes: [{ id: 'ckf-1', at: 1, strength: 1, media: { path: '/mid.png' } }],
						audio: [{ id: 'caud-1', start: 0, trim_start: 0, length: 2, media: { path: '/track.wav' } }]
					}
				},
				wanCapsLocal
			);
			const wire = buildDirectorSubmission(stale, wanCapsLocal);
			expect(wire.media).toEqual([]);
			expect(wire.audio).toEqual([]);
		});
	});

	it('regression: a lone-segment routed-chain document now derives to t2v (deriveDirectorMode) instead of a 1-shot chain', () => {
		// Pre-modeless, a single plain shot submitted as a "chain" was
		// indistinguishable, mechanically, from t2v (no continuation applies with
		// nothing to join) -- deriveDirectorMode makes that explicit rather than
		// carrying the chain machinery (frames/continuation) for one shot. Pinned
		// as JSON text: key order and the absence of any new key both matter.
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				global_prompt: 'anchor',
				chain: { fps: 16, segments: [{ id: 'c1', prompt: 'shot one', duration: 2.5 }] }
			},
			wanCaps
		);
		expect(JSON.stringify(buildDirectorSubmission(v, wanCaps))).toBe(
			'{"schema_version":1,"mode":"t2v","settings":{"fps":16,"duration":2.5,"seed":-1},' +
				'"segments":[{"id":"seg-1","prompt":"anchor. shot one","negative_prompt":"","start":0,"end":2.5,' +
				'"frames":null,"seed":null,"steps":null,"cfg":null,"loras":null}],"media":[],"audio":[],"ic_lora":[]}'
		);
	});

	it('regression: a genuinely multi-shot routed-chain document still serializes byte-identically', () => {
		// The same fixture as above, plus a second shot -- this is the exact
		// document every existing multi-shot Wan preset submits today.
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				global_prompt: 'anchor',
				chain: {
					fps: 16,
					segments: [
						{ id: 'c1', prompt: 'shot one', duration: 2.5 },
						{ id: 'c2', prompt: 'shot two', duration: 2.5 }
					]
				}
			},
			wanCaps
		);
		expect(JSON.stringify(buildDirectorSubmission(v, wanCaps))).toBe(
			'{"schema_version":1,"mode":"director","settings":{"fps":16,"duration":5,"seed":-1,' +
				'"continuation":{"source":"tail_frames","overlap_frames":4,"stitch":true}},' +
				'"segments":[{"id":"c1","prompt":"anchor. shot one","negative_prompt":"","start":null,"end":null,' +
				'"frames":40,"seed":null,"steps":null,"cfg":null,"loras":null},' +
				'{"id":"c2","prompt":"anchor. shot two","negative_prompt":"","start":null,"end":null,' +
				'"frames":40,"seed":null,"steps":null,"cfg":null,"loras":null}],"media":[],"audio":[],"ic_lora":[]}'
		);
	});

	it('regression: a timeline director audio track without a role serializes byte-identically', () => {
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				global_prompt: 'anchor',
				timeline: {
					duration: 6,
					fps: 24,
					audio: [{ id: 'a1', start: 0, trim_start: 0.5, length: 4, media: { path: '/aud.mp3' } }]
				}
			},
			caps
		);
		expect(JSON.stringify(buildDirectorSubmission(v, caps).audio)).toBe(
			'[{"id":"a1","start":0,"trim_start":0.5,"length":4,"media":{"path":"/aud.mp3"}}]'
		);
	});

	it('never includes ui in the wire doc', () => {
		const v = { ...base('t2v'), ui: { zoom: 5 } };
		const wire = buildDirectorSubmission(v, caps);
		expect((wire as unknown as { ui?: unknown }).ui).toBeUndefined();
	});

	it('seed is always -1', () => {
		for (const mode of ['t2v', 'i2v', 'flf', 'director'] as const) {
			const wire = buildDirectorSubmission(base(mode), caps);
			expect(wire.settings.seed).toBe(-1);
		}
	});
});

describe('representativeDirectorPrompt', () => {
	const caps = parseDirectorCapabilities(RAW_CAPS)!;
	const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!;

	it('t2v/i2v/flf return the global prompt', () => {
		for (const mode of ['t2v', 'i2v', 'flf'] as const) {
			const v = { ...normalizeDirectorValue({ mode }, caps), global_prompt: 'a scene' };
			expect(representativeDirectorPrompt(v, caps)).toBe('a scene');
		}
	});

	it('timeline director joins global prompt and segment texts with " | "', () => {
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				global_prompt: 'anchor',
				timeline: { segments: [{ id: 's1', start: 0, end: 1, text: 'seg one' }, { id: 's2', start: 1, end: 2, text: '' }] }
			},
			caps
		);
		expect(representativeDirectorPrompt(v, caps)).toBe('anchor | seg one');
	});

	it('routed-chain director joins global prompt and non-empty segment prompts', () => {
		const v = normalizeDirectorValue(
			{ mode: 'director', global_prompt: '', chain: { segments: [{ id: 'c1', prompt: 'first' }, { id: 'c2', prompt: 'second' }] } },
			wanCaps
		);
		expect(representativeDirectorPrompt(v, wanCaps)).toBe('first | second');
	});
});

// Pure reducer for the `update_video_director` chat tool's approved
// op list. `caps` decides timeline (LTX) vs chain (Wan) style the same way
// buildDirectorSubmission/validateDirector do -- it isn't carried on the
// value itself.
describe('applyDirectorOperations', () => {
	const caps = parseDirectorCapabilities(RAW_CAPS)!; // LTX-style timeline director
	const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!; // Wan-style routed chain

	it('is a no-op when operations is not an array', () => {
		const v = normalizeDirectorValue({}, caps);
		expect(applyDirectorOperations(v, null, caps)).toEqual(v);
		expect(applyDirectorOperations(v, undefined, caps)).toEqual(v);
		expect(applyDirectorOperations(v, 'nope', caps)).toEqual(v);
	});

	it('set_mode switches to a declared mode and ignores an invalid one', () => {
		const v = normalizeDirectorValue({ mode: 't2v' }, caps);
		expect(applyDirectorOperations(v, [{ op: 'set_mode', mode: 'director' }], caps).mode).toBe('director');
		expect(applyDirectorOperations(v, [{ op: 'set_mode', mode: 'bogus' }], caps).mode).toBe('t2v');
	});

	it('set_settings partially merges fps everywhere and duration onto simple/timeline only', () => {
		const v = normalizeDirectorValue({}, caps);
		const next = applyDirectorOperations(v, [{ op: 'set_settings', settings: { fps: 30, duration: 8 } }], caps);
		expect(next.simple.fps).toBe(30);
		expect(next.timeline.fps).toBe(30);
		expect(next.chain.fps).toBe(30);
		expect(next.simple.duration).toBe(8);
		expect(next.timeline.duration).toBe(8);
	});

	it('set_settings with only fps leaves duration untouched (partial merge)', () => {
		const v = normalizeDirectorValue({ simple: { duration: 5 } }, caps);
		const next = applyDirectorOperations(v, [{ op: 'set_settings', settings: { fps: 12 } }], caps);
		expect(next.simple.fps).toBe(12);
		expect(next.simple.duration).toBe(5);
	});

	it('set_settings tolerates fields with no editor destination (resolution, seed)', () => {
		const v = normalizeDirectorValue({}, caps);
		const next = applyDirectorOperations(v, [{ op: 'set_settings', settings: { resolution: '1024x576', seed: 42 } }], caps);
		expect(next).toEqual(v);
	});

	it('set_prompt replaces the global prompt and keeps prompt_segments consistent', () => {
		const v = normalizeDirectorValue({}, caps);
		const next = applyDirectorOperations(v, [{ op: 'set_prompt', prompt: 'a dog running' }], caps);
		expect(next.global_prompt).toBe('a dog running');
		expect(next.global_prompt_segments).toEqual([
			expect.objectContaining({ content: 'a dog running' })
		]);
	});

	it('set_prompt reuses the existing single segment id, and clears segments for an empty string', () => {
		const v = normalizeDirectorValue({ global_prompt_segments: [{ id: 'g1', content: 'old' }] }, caps);
		const next = applyDirectorOperations(v, [{ op: 'set_prompt', prompt: 'new text' }], caps);
		expect(next.global_prompt_segments).toEqual([expect.objectContaining({ id: 'g1', content: 'new text' })]);

		const cleared = applyDirectorOperations(next, [{ op: 'set_prompt', prompt: '' }], caps);
		expect(cleared.global_prompt_segments).toEqual([]);
		expect(cleared.global_prompt).toBe('');
	});

	it('set_negative_prompt mirrors set_prompt onto the negative prompt', () => {
		const v = normalizeDirectorValue({}, caps);
		const next = applyDirectorOperations(v, [{ op: 'set_negative_prompt', negative_prompt: 'blurry' }], caps);
		expect(next.negative_prompt).toBe('blurry');
		expect(next.negative_prompt_segments).toEqual([expect.objectContaining({ content: 'blurry' })]);
	});

	// The compact Direction/Negative rows in VideoDirectorEditor.svelte call
	// applySetPrompt/applySetNegativePrompt directly (not through
	// applyDirectorOperations), so this exercises that call path itself.
	describe('applySetPrompt / applySetNegativePrompt (direct calls, not via ops)', () => {
		it('round-trips an edit into global_prompt and global_prompt_segments consistently', () => {
			const v = normalizeDirectorValue({}, caps);
			const next = applySetPrompt(v, 'a dog running');
			expect(next.global_prompt).toBe('a dog running');
			expect(next.global_prompt_segments).toEqual([expect.objectContaining({ content: 'a dog running' })]);
		});

		it('round-trips an edit into negative_prompt and negative_prompt_segments consistently', () => {
			const v = normalizeDirectorValue({}, caps);
			const next = applySetNegativePrompt(v, 'blurry, low quality');
			expect(next.negative_prompt).toBe('blurry, low quality');
			expect(next.negative_prompt_segments).toEqual([
				expect.objectContaining({ content: 'blurry, low quality' })
			]);
		});

		it('reuses the existing single segment id across edits rather than minting a new one', () => {
			const v = normalizeDirectorValue({ global_prompt_segments: [{ id: 'g1', content: 'old' }] }, caps);
			const next = applySetPrompt(v, 'new text');
			expect(next.global_prompt_segments).toEqual([expect.objectContaining({ id: 'g1', content: 'new text' })]);
		});

		it('clears segments for an empty edit', () => {
			const v = normalizeDirectorValue({ global_prompt_segments: [{ id: 'g1', content: 'old' }] }, caps);
			const next = applySetPrompt(v, '');
			expect(next.global_prompt_segments).toEqual([]);
			expect(next.global_prompt).toBe('');
		});
	});

	describe('upsert_segment (timeline style)', () => {
		it('appends a new segment when the id is unknown, defaulting start/end to the full timeline range', () => {
			const v = normalizeDirectorValue({ timeline: { duration: 10 } }, caps);
			const next = applyDirectorOperations(v, [{ op: 'upsert_segment', segment: { id: 's1', prompt: 'a shot' } }], caps);
			expect(next.timeline.segments).toEqual([
				expect.objectContaining({ id: 's1', text: 'a shot', start: 0, end: 10 })
			]);
		});

		it('merges partial fields onto an existing segment by id, leaving others untouched', () => {
			const v = normalizeDirectorValue(
				{ timeline: { duration: 10, segments: [{ id: 's1', start: 0, end: 5, text: 'first' }] } },
				caps
			);
			const next = applyDirectorOperations(v, [{ op: 'upsert_segment', segment: { id: 's1', end: 8 } }], caps);
			expect(next.timeline.segments).toEqual([
				expect.objectContaining({ id: 's1', text: 'first', start: 0, end: 8 })
			]);
		});

		it('a chain-style op with no editor destination (frames/steps/cfg/seed/negative_prompt) does not throw and is ignored', () => {
			const v = normalizeDirectorValue({ timeline: { duration: 10 } }, caps);
			const next = applyDirectorOperations(
				v,
				[{ op: 'upsert_segment', segment: { id: 's1', frames: 48, steps: 20, cfg: 4, seed: 1, negative_prompt: 'x' } }],
				caps
			);
			expect(next.timeline.segments).toEqual([expect.objectContaining({ id: 's1', text: '', start: 0, end: 10 })]);
		});
	});

	describe('upsert_segment (chain style)', () => {
		it('appends a new segment converting frames to duration via chain.fps', () => {
			// Built by hand rather than via normalizeDirectorValue: an empty
			// chain.segments array there is refilled with a default segment
			// (normalizeDirectorValue's "never leave chain segments empty" rule),
			// which would mask a true append-into-empty-array assertion.
			const base = createDefaultDirectorValue(wanCaps);
			const v = { ...base, mode: 'director' as const, chain: { ...base.chain, fps: 16, segments: [] } };
			const next = applyDirectorOperations(
				v,
				[{ op: 'upsert_segment', segment: { id: 'c-new', prompt: 'shot one', frames: 32 } }],
				wanCaps
			);
			expect(next.chain.segments).toEqual([
				expect.objectContaining({ id: 'c-new', prompt: 'shot one', duration: 2 })
			]);
		});

		it('merges onto an existing segment by id, preserving loras/keyframe not touched by the op', () => {
			const v = normalizeDirectorValue(
				{
					mode: 'director',
					chain: {
						fps: 16,
						segments: [
							{ id: 'c1', prompt: 'old', duration: 3, keyframe: { path: '/kf.png' }, loras: { high: [], low: [] } }
						]
					}
				},
				wanCaps
			);
			const next = applyDirectorOperations(v, [{ op: 'upsert_segment', segment: { id: 'c1', prompt: 'new shot' } }], wanCaps);
			expect(next.chain.segments).toEqual([
				expect.objectContaining({
					id: 'c1',
					prompt: 'new shot',
					duration: 3,
					keyframe: { path: '/kf.png' },
					loras: { high: [], low: [] }
				})
			]);
		});

		it('a timeline-style op with no editor destination (start/end) does not throw and is ignored', () => {
			const base = createDefaultDirectorValue(wanCaps);
			const v = { ...base, mode: 'director' as const, chain: { ...base.chain, segments: [] } };
			const next = applyDirectorOperations(v, [{ op: 'upsert_segment', segment: { id: 'c1', prompt: 'a', start: 0, end: 5 } }], wanCaps);
			expect(next.chain.segments).toEqual([expect.objectContaining({ id: 'c1', prompt: 'a' })]);
		});
	});

	it('remove_segment removes by id from whichever tree has it', () => {
		const timelineV = normalizeDirectorValue(
			{ timeline: { segments: [{ id: 's1', start: 0, end: 1, text: 'a' }, { id: 's2', start: 1, end: 2, text: 'b' }] } },
			caps
		);
		const afterTimeline = applyDirectorOperations(timelineV, [{ op: 'remove_segment', id: 's1' }], caps);
		expect(afterTimeline.timeline.segments.map((s) => s.id)).toEqual(['s2']);

		const chainV = normalizeDirectorValue(
			{ mode: 'director', chain: { segments: [{ id: 'c1', prompt: 'a' }, { id: 'c2', prompt: 'b' }] } },
			wanCaps
		);
		const afterChain = applyDirectorOperations(chainV, [{ op: 'remove_segment', id: 'c1' }], wanCaps);
		expect(afterChain.chain.segments.map((s) => s.id)).toEqual(['c2']);
	});

	it('reorder_segments reorders by the given id list and appends unlisted ids afterward', () => {
		const v = normalizeDirectorValue(
			{
				timeline: {
					segments: [
						{ id: 's1', start: 0, end: 1, text: 'a' },
						{ id: 's2', start: 1, end: 2, text: 'b' },
						{ id: 's3', start: 2, end: 3, text: 'c' }
					]
				}
			},
			caps
		);
		const next = applyDirectorOperations(v, [{ op: 'reorder_segments', ids: ['s3', 's1'] }], caps);
		expect(next.timeline.segments.map((s) => s.id)).toEqual(['s3', 's1', 's2']);
	});

	it('reorder_segments ignores ids that do not exist in the list', () => {
		const v = normalizeDirectorValue(
			{ timeline: { segments: [{ id: 's1', start: 0, end: 1, text: 'a' }, { id: 's2', start: 1, end: 2, text: 'b' }] } },
			caps
		);
		const next = applyDirectorOperations(v, [{ op: 'reorder_segments', ids: ['ghost', 's2', 's1'] }], caps);
		expect(next.timeline.segments.map((s) => s.id)).toEqual(['s2', 's1']);
	});

	describe('upsert_media role mapping', () => {
		it('i2v: role "first" sets simple.start_image', () => {
			const v = normalizeDirectorValue({ mode: 'i2v' }, caps);
			const next = applyDirectorOperations(v, [{ op: 'upsert_media', media: { id: 'm1', role: 'first', path: '/s.png' } }], caps);
			expect(next.simple.start_image).toEqual({ path: '/s.png' });
		});

		it('flf: role "first"/"last" set first_frame/last_frame respectively', () => {
			const v = normalizeDirectorValue({ mode: 'flf' }, caps);
			const next = applyDirectorOperations(
				v,
				[
					{ op: 'upsert_media', media: { id: 'm1', role: 'first', path: '/f.png' } },
					{ op: 'upsert_media', media: { id: 'm2', role: 'last', path: '/l.png' } }
				],
				caps
			);
			expect(next.simple.first_frame).toEqual({ path: '/f.png' });
			expect(next.simple.last_frame).toEqual({ path: '/l.png' });
		});

		it('timeline director: role "keyframe" maps to a free keyframe, "first"/"last" map directly', () => {
			const v = normalizeDirectorValue({ mode: 'director' }, caps);
			const next = applyDirectorOperations(
				v,
				[
					{ op: 'upsert_media', media: { id: 'k1', role: 'first', path: '/a.png', at: 0, strength: 1 } },
					{ op: 'upsert_media', media: { id: 'k2', role: 'keyframe', path: '/b.png', at: 3, strength: 0.5 } }
				],
				caps
			);
			expect(next.timeline.keyframes).toEqual([
				{ id: 'k1', start: 0, role: 'first', strength: 1, media: { path: '/a.png' } },
				{ id: 'k2', start: 3, role: 'free', strength: 0.5, media: { path: '/b.png' } }
			]);
		});

		it('timeline director: upserting an existing keyframe id merges by id rather than appending', () => {
			const v = normalizeDirectorValue(
				{ mode: 'director', timeline: { keyframes: [{ id: 'k1', start: 0, role: 'first', strength: 1, media: { path: '/old.png' } }] } },
				caps
			);
			const next = applyDirectorOperations(v, [{ op: 'upsert_media', media: { id: 'k1', role: 'first', path: '/new.png' } }], caps);
			expect(next.timeline.keyframes).toHaveLength(1);
			expect(next.timeline.keyframes[0]).toEqual({ id: 'k1', start: 0, role: 'first', strength: 1, media: { path: '/new.png' } });
		});

		it('chain director with keyframes anywhere: role "keyframe" upserts a placed keyframe by id', () => {
			const hybridCaps = parseDirectorCapabilities(HYBRID_RAW_CAPS)!;
			const v = normalizeDirectorValue({ mode: 'director', chain: { segments: [{ id: 'c1', prompt: 'a' }] } }, hybridCaps);
			const added = applyDirectorOperations(
				v,
				[{ op: 'upsert_media', media: { id: 'ckf-1', role: 'keyframe', path: '/mid.png', at: 2, strength: 0.6 } }],
				hybridCaps
			);
			expect(added.chain.keyframes).toEqual([{ id: 'ckf-1', at: 2, strength: 0.6, media: { path: '/mid.png' } }]);

			const merged = applyDirectorOperations(
				added,
				[{ op: 'upsert_media', media: { id: 'ckf-1', role: 'keyframe', path: '/other.png' } }],
				hybridCaps
			);
			expect(merged.chain.keyframes).toEqual([{ id: 'ckf-1', at: 2, strength: 0.6, media: { path: '/other.png' } }]);

			const removed = applyDirectorOperations(merged, [{ op: 'remove_media', id: 'ckf-1' }], hybridCaps);
			expect(removed.chain.keyframes).toEqual([]);
		});

		it('chain director without the anywhere capability ignores a "keyframe" role op', () => {
			const v = normalizeDirectorValue({ mode: 'director', chain: { segments: [{ id: 'c1', prompt: 'a' }] } }, wanCaps);
			const next = applyDirectorOperations(
				v,
				[{ op: 'upsert_media', media: { id: 'ckf-1', role: 'keyframe', path: '/mid.png', at: 2 } }],
				wanCaps
			);
			expect(next.chain.keyframes).toEqual([]);
		});

		it('chain director: only role "first" on segment 0 is supported; other roles are ignored', () => {
			const v = normalizeDirectorValue({ mode: 'director', chain: { segments: [{ id: 'c1', prompt: 'a' }] } }, wanCaps);
			const next = applyDirectorOperations(
				v,
				[{ op: 'upsert_media', media: { id: 'm1', role: 'first', path: '/kf.png', strength: 0.8 } }],
				wanCaps
			);
			expect(next.chain.segments[0].keyframe).toEqual({ path: '/kf.png' });
			expect(next.chain.segments[0].keyframe_strength).toBe(0.8);

			const untouched = applyDirectorOperations(v, [{ op: 'upsert_media', media: { id: 'm2', role: 'last', path: '/nope.png' } }], wanCaps);
			expect(untouched.chain.segments[0].keyframe).toBeNull();
		});
	});

	describe('upsert_media form_ref marker (Stage B reference media)', () => {
		it('stores a form_ref pointer instead of the resolved path when the op carries one', () => {
			const v = normalizeDirectorValue({ mode: 'i2v' }, caps);
			const next = applyDirectorOperations(
				v,
				[{
					op: 'upsert_media',
					media: { id: 'm1', role: 'first', path: '/resolved/hero.png', form_ref: { field: 'reference_image', path: 'uploads/hero.png' } }
				}],
				caps
			);
			expect(next.simple.start_image).toEqual({ form_ref: { field: 'reference_image', path: 'uploads/hero.png' } });
		});

		it('falls back to a plain embedded path when no form_ref is present', () => {
			const v = normalizeDirectorValue({ mode: 'i2v' }, caps);
			const next = applyDirectorOperations(
				v,
				[{ op: 'upsert_media', media: { id: 'm1', role: 'first', path: '/s.png' } }],
				caps
			);
			expect(next.simple.start_image).toEqual({ path: '/s.png' });
		});

		it('ignores a malformed form_ref (missing field/path) and falls back to path', () => {
			const v = normalizeDirectorValue({ mode: 'i2v' }, caps);
			const next = applyDirectorOperations(
				v,
				[{ op: 'upsert_media', media: { id: 'm1', role: 'first', path: '/s.png', form_ref: { field: 'x' } } }],
				caps
			);
			expect(next.simple.start_image).toEqual({ path: '/s.png' });
		});
	});

	it('remove_media removes a timeline keyframe by id and no-ops for simple/chain media', () => {
		const v = normalizeDirectorValue(
			{ mode: 'director', timeline: { keyframes: [{ id: 'k1', start: 0, role: 'first', strength: 1, media: { path: '/a.png' } }] } },
			caps
		);
		const next = applyDirectorOperations(v, [{ op: 'remove_media', id: 'k1' }], caps);
		expect(next.timeline.keyframes).toEqual([]);

		const i2v = normalizeDirectorValue({ mode: 'i2v', simple: { start_image: { path: '/s.png' } } }, caps);
		const i2vAfter = applyDirectorOperations(i2v, [{ op: 'remove_media', id: 'anything' }], caps);
		expect(i2vAfter.simple.start_image).toEqual({ path: '/s.png' });
	});

	it('ignores unrecognized op types while still applying the rest of the list (forward compat)', () => {
		const v = normalizeDirectorValue({ mode: 't2v' }, caps);
		const next = applyDirectorOperations(
			v,
			[
				{ op: 'set_prompt', prompt: 'kept' },
				{ op: 'some_future_op', whatever: true },
				{ op: 'set_mode', mode: 'director' }
			],
			caps
		);
		expect(next.global_prompt).toBe('kept');
		expect(next.mode).toBe('director');
	});

	it('tolerates malformed op entries (missing fields, wrong types, non-objects) without throwing', () => {
		const v = normalizeDirectorValue({ mode: 't2v' }, caps);
		expect(() =>
			applyDirectorOperations(
				v,
				[null, 'nope', 42, {}, { op: 123 }, { op: 'upsert_segment' }, { op: 'upsert_media', media: { id: 'x' } }],
				caps
			)
		).not.toThrow();
	});

	it('is pure: never mints ids and is byte-deterministic for identical inputs', () => {
		const v = normalizeDirectorValue({ mode: 'director', chain: { segments: [] } }, wanCaps);
		const ops = [{ op: 'upsert_segment', segment: { id: 'c-new', prompt: 'a shot', frames: 16 } }];
		const a = applyDirectorOperations(v, ops, wanCaps);
		const b = applyDirectorOperations(v, ops, wanCaps);
		expect(JSON.stringify(a)).toBe(JSON.stringify(b));
	});
});

describe('applyDirectorSegmentPrompt', () => {
	const caps = parseDirectorCapabilities(RAW_CAPS)!; // LTX-style timeline director
	const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!; // Wan-style routed chain

	it('id match: replaces the prompt on a chain segment, preserving duration/loras/keyframe', () => {
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				chain: {
					fps: 16,
					segments: [
						{ id: 'c1', prompt: 'old', duration: 3, keyframe: { path: '/kf.png' }, loras: { high: [], low: [] } },
						{ id: 'c2', prompt: 'second', duration: 4 }
					]
				}
			},
			wanCaps
		);
		const next = applyDirectorSegmentPrompt(v, wanCaps, { segmentId: 'c1', segmentIndex: 0, content: 'new shot' });
		expect(next).not.toBeNull();
		expect(next!.chain.segments).toEqual([
			expect.objectContaining({
				id: 'c1',
				prompt: 'new shot',
				duration: 3,
				keyframe: { path: '/kf.png' },
				loras: { high: [], low: [] }
			}),
			expect.objectContaining({ id: 'c2', prompt: 'second', duration: 4 })
		]);
	});

	it('id match: replaces the prompt on a timeline segment, preserving start/end', () => {
		const v = normalizeDirectorValue(
			{ timeline: { duration: 10, segments: [{ id: 's1', start: 2, end: 6, text: 'old text' }] } },
			caps
		);
		const next = applyDirectorSegmentPrompt(v, caps, { segmentId: 's1', segmentIndex: 0, content: 'new text' });
		expect(next).not.toBeNull();
		expect(next!.timeline.segments).toEqual([expect.objectContaining({ id: 's1', start: 2, end: 6, text: 'new text' })]);
	});

	it('id miss, valid index: falls back to the segment at that position', () => {
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				chain: { fps: 16, segments: [{ id: 'c1', prompt: 'a' }, { id: 'c2', prompt: 'b' }] }
			},
			wanCaps
		);
		const next = applyDirectorSegmentPrompt(v, wanCaps, { segmentId: 'unknown-id', segmentIndex: 1, content: 'rewritten' });
		expect(next).not.toBeNull();
		expect(next!.chain.segments.map((s) => s.prompt)).toEqual(['a', 'rewritten']);
	});

	it('both id and index miss: returns null and never appends a segment', () => {
		const v = normalizeDirectorValue(
			{ mode: 'director', chain: { fps: 16, segments: [{ id: 'c1', prompt: 'a' }] } },
			wanCaps
		);
		const next = applyDirectorSegmentPrompt(v, wanCaps, { segmentId: 'nope', segmentIndex: 9, content: 'ignored' });
		expect(next).toBeNull();
	});

	it('index miss (out of range) with an id miss also returns null on the timeline list', () => {
		const v = normalizeDirectorValue({ timeline: { duration: 10, segments: [{ id: 's1', start: 0, end: 10, text: 'a' }] } }, caps);
		const next = applyDirectorSegmentPrompt(v, caps, { segmentId: 'nope', segmentIndex: -1, content: 'ignored' });
		expect(next).toBeNull();
	});

	it('picks chain.segments vs timeline.segments by caps.segmentRouting', () => {
		const chainDoc = normalizeDirectorValue(
			{ mode: 'director', chain: { fps: 16, segments: [{ id: 'c1', prompt: 'a' }] }, timeline: { segments: [{ id: 's1', start: 0, end: 5, text: 'unrelated' }] } },
			wanCaps
		);
		const next = applyDirectorSegmentPrompt(chainDoc, wanCaps, { segmentId: 'c1', segmentIndex: 0, content: 'chain wins' });
		expect(next!.chain.segments[0].prompt).toBe('chain wins');
		expect(next!.timeline.segments[0].text).toBe('unrelated');
	});
});

// The ops the chat tool gained with the current chain-composer contract:
// shot lengths in seconds, join control, chain-wide audio and continuation.
describe('applyDirectorOperations (chain composer ops)', () => {
	const caps = parseDirectorCapabilities(RAW_CAPS)!; // timeline director, audio capable
	const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!; // routed chain, no audio
	const hybridCaps = parseDirectorCapabilities(HYBRID_RAW_CAPS)!; // routed chain, audio + keyframes anywhere

	const twoShotChain = (c = hybridCaps) =>
		normalizeDirectorValue(
			{ mode: 'director', chain: { fps: 16, segments: [{ id: 'c1', prompt: 'a' }, { id: 'c2', prompt: 'b' }] } },
			c
		);

	describe('upsert_segment length and join', () => {
		it('takes duration in seconds over a frames count that disagrees with it', () => {
			// 96 frames is 6s at the chain's 16 fps -- the seconds the tool was
			// given must survive, not the count derived at some other fps.
			const v = twoShotChain();
			const next = applyDirectorOperations(
				v,
				[{ op: 'upsert_segment', segment: { id: 'c1', duration: 7.5, frames: 96 } }],
				hybridCaps
			);
			expect(next.chain.segments[0].duration).toBe(7.5);
		});

		it('still accepts a frames-only op, converting at the chain fps', () => {
			const v = twoShotChain();
			const next = applyDirectorOperations(v, [{ op: 'upsert_segment', segment: { id: 'c1', frames: 32 } }], hybridCaps);
			expect(next.chain.segments[0].duration).toBe(2);
		});

		it('sets sub_type_override to force a hard cut and clears it back to continuing', () => {
			const v = twoShotChain();
			const cut = applyDirectorOperations(
				v,
				[{ op: 'upsert_segment', segment: { id: 'c2', sub_type_override: 't2v' } }],
				hybridCaps
			);
			expect(cut.chain.segments[1].sub_type_override).toBe('t2v');
			expect(deriveChainSegmentSubType(cut.chain.segments[1], 1)).toBe('t2v');

			const restored = applyDirectorOperations(
				cut,
				[{ op: 'upsert_segment', segment: { id: 'c2', sub_type_override: null } }],
				hybridCaps
			);
			expect(restored.chain.segments[1].sub_type_override).toBeNull();
			expect(deriveChainSegmentSubType(restored.chain.segments[1], 1)).toBe('chain');
		});

		it('leaves an existing override alone when the op does not mention it', () => {
			const v = applyDirectorOperations(
				twoShotChain(),
				[{ op: 'upsert_segment', segment: { id: 'c2', sub_type_override: 't2v' } }],
				hybridCaps
			);
			const next = applyDirectorOperations(v, [{ op: 'upsert_segment', segment: { id: 'c2', prompt: 'rewritten' } }], hybridCaps);
			expect(next.chain.segments[1].sub_type_override).toBe('t2v');
			expect(next.chain.segments[1].prompt).toBe('rewritten');
		});
	});

	describe('audio', () => {
		it('upsert_audio adds a chain track and merges a later op by id', () => {
			const added = applyDirectorOperations(
				twoShotChain(),
				[{ op: 'upsert_audio', audio: { id: 'audio_1', role: 'mux', path: '/waves.mp3', start: 1, trim_start: 0.5, length: 8 } }],
				hybridCaps
			);
			expect(added.chain.audio).toEqual([
				{ id: 'audio_1', start: 1, trim_start: 0.5, length: 8, media: { path: '/waves.mp3' }, role: 'mux' }
			]);

			const merged = applyDirectorOperations(
				added,
				[{ op: 'upsert_audio', audio: { id: 'audio_1', path: '/gulls.mp3' } }],
				hybridCaps
			);
			expect(merged.chain.audio).toHaveLength(1);
			expect(merged.chain.audio[0]).toEqual({
				id: 'audio_1', start: 1, trim_start: 0.5, length: 8, media: { path: '/gulls.mp3' }, role: 'mux'
			});
		});

		it('upsert_audio lands on the timeline composition when the preset is not routed', () => {
			const v = normalizeDirectorValue({ mode: 'director', timeline: { segments: [] } }, caps);
			const next = applyDirectorOperations(
				v,
				[{ op: 'upsert_audio', audio: { id: 'audio_1', role: 'mux', path: '/waves.mp3', length: 5 } }],
				caps
			);
			expect(next.timeline.audio).toEqual([
				{ id: 'audio_1', start: 0, trim_start: 0, length: 5, media: { path: '/waves.mp3' }, role: 'mux' }
			]);
			expect(next.chain.audio).toEqual([]);
		});

		it('ignores audio ops where the mode does not declare the capability', () => {
			const v = twoShotChain(wanCaps);
			const next = applyDirectorOperations(
				v,
				[{ op: 'upsert_audio', audio: { id: 'audio_1', role: 'mux', path: '/waves.mp3', length: 5 } }],
				wanCaps
			);
			expect(next.chain.audio).toEqual([]);
		});

		it('remove_audio drops the track by id', () => {
			const added = applyDirectorOperations(
				twoShotChain(),
				[{ op: 'upsert_audio', audio: { id: 'audio_1', role: 'mux', path: '/waves.mp3', length: 5 } }],
				hybridCaps
			);
			const removed = applyDirectorOperations(added, [{ op: 'remove_audio', id: 'audio_1' }], hybridCaps);
			expect(removed.chain.audio).toEqual([]);
		});
	});

	describe('continuation', () => {
		it('set_continuation merges onto the current chain settings', () => {
			const v = twoShotChain();
			expect(v.chain.continuation).toEqual({ overlap_frames: 4, stitch: true });

			const next = applyDirectorOperations(
				v,
				[{ op: 'set_continuation', continuation: { overlap_frames: 6 } }],
				hybridCaps
			);
			expect(next.chain.continuation).toEqual({ overlap_frames: 6, stitch: true });

			const unstitched = applyDirectorOperations(next, [{ op: 'set_continuation', continuation: { stitch: false } }], hybridCaps);
			expect(unstitched.chain.continuation).toEqual({ overlap_frames: 6, stitch: false });
		});

		it('is ignored in timeline style, which has no shot joins', () => {
			const v = normalizeDirectorValue({ mode: 'director' }, caps);
			const next = applyDirectorOperations(v, [{ op: 'set_continuation', continuation: { stitch: false } }], caps);
			expect(next).toEqual(v);
		});
	});

	it("remove_media clears the first shot's start image under the id the read tool reports", () => {
		const v = normalizeDirectorValue(
			{ mode: 'director', chain: { segments: [{ id: 'c1', prompt: 'a', keyframe: { path: '/open.png' } }, { id: 'c2', prompt: 'b' }] } },
			hybridCaps
		);
		expect(v.chain.segments[0].keyframe).toEqual({ path: '/open.png' });

		const next = applyDirectorOperations(v, [{ op: 'remove_media', id: 'kf-c1' }], hybridCaps);
		expect(next.chain.segments[0].keyframe).toBeNull();

		const untouched = applyDirectorOperations(v, [{ op: 'remove_media', id: 'kf-c2' }], hybridCaps);
		expect(untouched.chain.segments[0].keyframe).toEqual({ path: '/open.png' });
	});

	it('a start image aimed at a later shot lands on THAT shot, not the first', () => {
		// Join-aware, not index-pinned: any segment may carry its own leading
		// image (chainSegmentEdgeAllowances) -- the well only ever renders where
		// that's already eligible, but a direct op (chat tool, this applier)
		// still just targets whatever segment_id it's given.
		const v = twoShotChain();
		const next = applyDirectorOperations(
			v,
			[{ op: 'upsert_media', media: { id: 'm1', role: 'first', segment_id: 'c2', path: '/x.png' } }],
			hybridCaps
		);
		expect(next.chain.segments[0].keyframe).toBeNull();
		expect(next.chain.segments[1].keyframe).toEqual({ path: '/x.png' });
	});

	it('applies a whole one-call composition deterministically', () => {
		// The editor always holds at least one shot, so a fresh composition
		// updates that one and appends the rest -- exactly what the tool emits
		// after reading the document.
		const v = normalizeDirectorValue({ mode: 'director', chain: { fps: 16, segments: [{ id: 'seg_1', prompt: '' }] } }, hybridCaps);
		const ops = [
			{ op: 'set_prompt', prompt: 'a lighthouse at dusk' },
			{ op: 'upsert_segment', segment: { id: 'seg_1', prompt: 'the beam wakes', duration: 6, frames: 96 } },
			{ op: 'upsert_segment', segment: { id: 'seg_2', prompt: 'gulls scatter', duration: 6, frames: 96 } },
			{ op: 'upsert_segment', segment: { id: 'seg_3', prompt: 'cut to the keeper', duration: 6, frames: 96, sub_type_override: 't2v' } },
			{ op: 'upsert_media', media: { id: 'media_1', role: 'keyframe', at: 12, strength: 0.7, path: '/kf.png' } },
			{ op: 'upsert_audio', audio: { id: 'audio_1', role: 'mux', path: '/waves.mp3', length: 18 } },
			{ op: 'set_continuation', continuation: { overlap_frames: 6, stitch: true } }
		];
		const a = applyDirectorOperations(v, ops, hybridCaps);
		const b = applyDirectorOperations(v, ops, hybridCaps);
		expect(JSON.stringify(a)).toBe(JSON.stringify(b));

		expect(a.global_prompt).toBe('a lighthouse at dusk');
		expect(a.chain.segments.map((s) => s.duration)).toEqual([6, 6, 6]);
		expect(a.chain.segments.map((s, i) => deriveChainSegmentSubType(s, i))).toEqual(['t2v', 'chain', 't2v']);
		expect(a.chain.keyframes).toEqual([{ id: 'media_1', at: 12, strength: 0.7, media: { path: '/kf.png' } }]);
		expect(a.chain.audio).toEqual([
			{ id: 'audio_1', start: 0, trim_start: 0, length: 18, media: { path: '/waves.mp3' }, role: 'mux' }
		]);
		expect(a.chain.continuation).toEqual({ overlap_frames: 6, stitch: true });
	});
});

// Stage B: a Director media entry can point at an item living on the
// generate form's own media-loader field(s) instead of embedding its own
// copy. These are the pure helpers -- resolution, display, the picker's
// option list, and the wire-doc dereference done right before submission.
describe('Stage B form media references', () => {
	const heroFormData = {
		reference_image: { path: 'uploads/hero.png', name: 'hero.png', type: 'image', label: 'Hero' },
		gallery: [
			{ path: 'uploads/a.png', name: 'a.png', type: 'image', label: 'First' },
			{ path: 'uploads/b.png', name: 'b.png', type: 'video' }
		],
		checkpoint: { modelPath: 'models/checkpoints/sdxl.safetensors' }
	};

	function emptyWireDoc(overrides: Partial<VideoDirectorWireDoc> = {}): VideoDirectorWireDoc {
		return {
			schema_version: 1,
			mode: 't2v',
			settings: { fps: 24, duration: 5, seed: -1 },
			segments: [],
			media: [],
			audio: [],
			ic_lora: [],
			...overrides
		};
	}

	describe('isFormMediaRef', () => {
		it('recognizes a well-formed form_ref', () => {
			expect(isFormMediaRef({ form_ref: { field: 'x', path: 'y' } })).toBe(true);
		});

		it('rejects an embedded MediaRef, null, and a malformed form_ref', () => {
			expect(isFormMediaRef({ path: '/a.png' })).toBe(false);
			expect(isFormMediaRef(null)).toBe(false);
			expect(isFormMediaRef({ form_ref: { field: 'x' } })).toBe(false);
			expect(isFormMediaRef({ form_ref: 'x' })).toBe(false);
		});
	});

	describe('resolveFormMediaItem', () => {
		it('resolves a single-field value by path', () => {
			expect(resolveFormMediaItem('reference_image', 'uploads/hero.png', heroFormData)).toEqual(heroFormData.reference_image);
		});

		it('resolves a multiple-field array entry by path', () => {
			expect(resolveFormMediaItem('gallery', 'uploads/b.png', heroFormData)).toEqual(heroFormData.gallery[1]);
		});

		it('returns null when the field is gone or the item is no longer on it', () => {
			expect(resolveFormMediaItem('reference_image', 'uploads/other.png', heroFormData)).toBeNull();
			expect(resolveFormMediaItem('missing_field', 'uploads/hero.png', heroFormData)).toBeNull();
			expect(resolveFormMediaItem('reference_image', 'uploads/hero.png', null)).toBeNull();
		});
	});

	describe('resolveDirectorMediaDisplay', () => {
		it('is "empty" for null/undefined', () => {
			expect(resolveDirectorMediaDisplay(null, heroFormData)).toEqual({ kind: 'empty' });
			expect(resolveDirectorMediaDisplay(undefined, heroFormData)).toEqual({ kind: 'empty' });
		});

		it('is "embedded" for a plain MediaRef, verbatim', () => {
			const media: MediaRef = { path: '/local.png', name: 'local.png' };
			expect(resolveDirectorMediaDisplay(media, heroFormData)).toEqual({ kind: 'embedded', media });
		});

		it('is "form_ref" with the resolved item when the reference still resolves', () => {
			const value = { form_ref: { field: 'reference_image', path: 'uploads/hero.png' } };
			expect(resolveDirectorMediaDisplay(value, heroFormData)).toEqual({
				kind: 'form_ref',
				media: heroFormData.reference_image,
				field: 'reference_image'
			});
		});

		it('is "broken" with the field name when the reference no longer resolves', () => {
			const value = { form_ref: { field: 'reference_image', path: 'uploads/gone.png' } };
			expect(resolveDirectorMediaDisplay(value, heroFormData)).toEqual({ kind: 'broken', field: 'reference_image' });
		});
	});

	describe('collectFormMediaOptions', () => {
		it('collects single and multi-field media items, skipping non-media field values', () => {
			const options = collectFormMediaOptions(heroFormData);
			expect(options.map((o) => o.item.path)).toEqual(['uploads/hero.png', 'uploads/a.png', 'uploads/b.png']);
			expect(options[0]).toEqual({ field: 'reference_image', fieldLabel: 'Reference Image', item: heroFormData.reference_image });
		});

		it('filters to a requested kind, treating an absent type as never excluded', () => {
			const withUntyped = { ...heroFormData, extra: { path: 'uploads/c.png' } };
			const images = collectFormMediaOptions(withUntyped, 'image');
			expect(images.map((o) => o.item.path)).toEqual(['uploads/hero.png', 'uploads/a.png', 'uploads/c.png']);
		});

		it('returns [] for null/undefined form data', () => {
			expect(collectFormMediaOptions(null)).toEqual([]);
			expect(collectFormMediaOptions(undefined)).toEqual([]);
		});
	});

	describe('formMediaOptionKeys', () => {
		it('keys distinct field:path pairs by the pair itself', () => {
			const options = collectFormMediaOptions(heroFormData);
			expect(formMediaOptionKeys(options)).toEqual(['reference_image:uploads/hero.png', 'gallery:uploads/a.png', 'gallery:uploads/b.png']);
		});

		it('suffixes every occurrence after the first when the same library resource is picked twice on one field -- the crash this guards against', () => {
			const doc = {
				references: [
					{ path: 'uploads/dup.jpg', name: 'dup.jpg', type: 'image' },
					{ path: 'uploads/other.jpg', name: 'other.jpg', type: 'image' },
					{ path: 'uploads/dup.jpg', name: 'dup.jpg', type: 'image' }
				]
			};
			const options = collectFormMediaOptions(doc);
			const keys = formMediaOptionKeys(options);
			expect(keys).toEqual(['references:uploads/dup.jpg', 'references:uploads/other.jpg', 'references:uploads/dup.jpg#2']);
			expect(new Set(keys).size).toBe(keys.length);
		});
	});

	describe('dereferenceFormMediaRefs', () => {
		it('replaces a resolvable form_ref with a full copy of the current form item', () => {
			const doc = emptyWireDoc({
				media: [{
					id: 'm1', role: 'first', segment_id: 'seg-1', at: 0, strength: 1,
					media: { form_ref: { field: 'reference_image', path: 'uploads/hero.png' } }
				}]
			});
			const { doc: resolved, errors } = dereferenceFormMediaRefs(doc, heroFormData);
			expect(errors).toEqual([]);
			expect(resolved.media[0].media).toEqual(heroFormData.reference_image);
		});

		it('leaves an already-embedded media value untouched', () => {
			const embedded: MediaRef = { path: '/local.png' };
			const doc = emptyWireDoc({
				media: [{ id: 'm1', role: 'first', segment_id: 'seg-1', at: 0, strength: 1, media: embedded }]
			});
			const { doc: resolved, errors } = dereferenceFormMediaRefs(doc, heroFormData);
			expect(errors).toEqual([]);
			expect(resolved.media[0].media).toBe(embedded);
		});

		it('resolves audio and ic_lora reference form_refs too', () => {
			const doc = emptyWireDoc({
				audio: [{
					id: 'a1', role: 'mux', start: 0, trim_start: 0, length: 5,
					media: { form_ref: { field: 'gallery', path: 'uploads/b.png' } }
				}],
				ic_lora: [{
					id: 'i1', lora: { model: 'x.safetensors', strength: 1 },
					reference: { form_ref: { field: 'gallery', path: 'uploads/a.png' } }, strength: 1
				}]
			});
			const { doc: resolved, errors } = dereferenceFormMediaRefs(doc, heroFormData);
			expect(errors).toEqual([]);
			expect(resolved.audio[0].media).toEqual(heroFormData.gallery[1]);
			expect(resolved.ic_lora[0].reference).toEqual(heroFormData.gallery[0]);
		});

		it('an ic_lora entry with no reference is passed through untouched', () => {
			const doc = emptyWireDoc({
				ic_lora: [{ id: 'i1', lora: { model: 'x.safetensors', strength: 1 }, reference: null, strength: 1 }]
			});
			const { doc: resolved, errors } = dereferenceFormMediaRefs(doc, heroFormData);
			expect(errors).toEqual([]);
			expect(resolved.ic_lora[0].reference).toBeNull();
		});

		it('reports a structured error (not a throw) for an unresolvable reference, and leaves the ref in place', () => {
			const doc = emptyWireDoc({
				media: [{
					id: 'm1', role: 'first', segment_id: 'seg-1', at: 0, strength: 1,
					media: { form_ref: { field: 'reference_image', path: 'uploads/gone.png' } }
				}]
			});
			const { doc: resolved, errors } = dereferenceFormMediaRefs(doc, heroFormData);
			expect(errors).toEqual(['media[0]: missing from form field "reference_image"']);
			expect(resolved.media[0].media).toEqual({ form_ref: { field: 'reference_image', path: 'uploads/gone.png' } });
		});

		it('collects one error per unresolvable reference across media/audio/ic_lora', () => {
			const doc = emptyWireDoc({
				media: [{ id: 'm1', role: 'first', segment_id: null, at: 0, strength: 1, media: { form_ref: { field: 'a', path: 'x' } } }],
				audio: [{ id: 'a1', role: 'mux', start: 0, trim_start: 0, length: 5, media: { form_ref: { field: 'b', path: 'y' } } }]
			});
			const { errors } = dereferenceFormMediaRefs(doc, heroFormData);
			expect(errors).toEqual([
				'media[0]: missing from form field "a"',
				'audio[0]: missing from form field "b"'
			]);
		});

		it('is pure and byte-deterministic for identical inputs', () => {
			const doc = emptyWireDoc({
				media: [{
					id: 'm1', role: 'first', segment_id: 'seg-1', at: 0, strength: 1,
					media: { form_ref: { field: 'reference_image', path: 'uploads/hero.png' } }
				}]
			});
			const a = dereferenceFormMediaRefs(doc, heroFormData);
			const b = dereferenceFormMediaRefs(doc, heroFormData);
			expect(JSON.stringify(a)).toBe(JSON.stringify(b));
		});
	});
});

// The Stage & Rail editor has no mode switch: `mode` is a
// derived read of `chain`/`timeline`, never a stored user choice. These cover
// deriveDirectorMode's classification, toModelessDirectorValue's projection
// (idempotency, no-resurrection, and losslessness vs. the mode a human once
// picked by hand), and that the derived mode stays true after an edit even
// while the stored `mode` field lags behind.

describe('deriveDirectorMode', () => {
	const caps = parseDirectorCapabilities(RAW_CAPS)!; // timeline routing, flf enabled
	const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!; // chain routing, flf enabled
	const hybridCaps = parseDirectorCapabilities(HYBRID_RAW_CAPS)!; // chain routing, flf NOT declared

	it('timeline routing: a bare single shot derives t2v', () => {
		const v = normalizeDirectorValue({ mode: 'director' }, caps);
		expect(deriveDirectorMode(v, caps)).toBe('t2v');
	});

	it('timeline routing: only a leading (first-role) keyframe derives i2v', () => {
		const v = normalizeDirectorValue(
			{ mode: 'director', timeline: { keyframes: [{ id: 'k1', start: 0, role: 'first', strength: 1, media: media('/s.png') }] } },
			caps
		);
		expect(deriveDirectorMode(v, caps)).toBe('i2v');
	});

	it('timeline routing: leading + trailing keyframes derive flf when the capability allows it', () => {
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				timeline: {
					keyframes: [
						{ id: 'k1', start: 0, role: 'first', strength: 1, media: media('/s.png') },
						{ id: 'k2', start: 5, role: 'last', strength: 1, media: media('/e.png') }
					]
				}
			},
			caps
		);
		expect(deriveDirectorMode(v, caps)).toBe('flf');
	});

	it('timeline routing: a leading-only keyframe stays director when the capability does not declare i2v', () => {
		const noI2vCaps = parseDirectorCapabilities({
			modes: { t2v: {}, flf: {}, director: {} },
			limits: { default_duration: 5, default_fps: 24 }
		})!;
		const v = normalizeDirectorValue(
			{ mode: 'director', timeline: { keyframes: [{ id: 'k1', start: 0, role: 'first', strength: 1, media: media('/s.png') }] } },
			noI2vCaps
		);
		expect(deriveDirectorMode(v, noI2vCaps)).toBe('director');
	});

	it('timeline routing: leading + trailing stays director when the capability does not declare flf', () => {
		const noFlfCaps = parseDirectorCapabilities({
			modes: { t2v: {}, i2v: {}, director: {} },
			limits: { default_duration: 5, default_fps: 24 }
		})!;
		const v = normalizeDirectorValue(
			{
				mode: 'director',
				timeline: {
					keyframes: [
						{ id: 'k1', start: 0, role: 'first', strength: 1, media: media('/s.png') },
						{ id: 'k2', start: 5, role: 'last', strength: 1, media: media('/e.png') }
					]
				}
			},
			noFlfCaps
		);
		expect(deriveDirectorMode(v, noFlfCaps)).toBe('director');
	});

	it('timeline routing: more than one shot, or a free keyframe/audio/ic_lora extra, is director', () => {
		const twoShots = normalizeDirectorValue(
			{
				mode: 'director',
				timeline: { segments: [{ id: 's1', start: 0, end: 2, text: 'a' }, { id: 's2', start: 2, end: 4, text: 'b' }] }
			},
			caps
		);
		expect(deriveDirectorMode(twoShots, caps)).toBe('director');

		const freeKeyframe = normalizeDirectorValue(
			{ mode: 'director', timeline: { keyframes: [{ id: 'k1', start: 2, role: 'free', strength: 1, media: media('/m.png') }] } },
			caps
		);
		expect(deriveDirectorMode(freeKeyframe, caps)).toBe('director');

		const withAudio = normalizeDirectorValue(
			{ mode: 'director', timeline: { audio: [{ id: 'a1', start: 0, trim_start: 0, length: 2, media: media('/a.wav') }] } },
			caps
		);
		expect(deriveDirectorMode(withAudio, caps)).toBe('director');

		const withIcLora = normalizeDirectorValue(
			{ mode: 'director', timeline: { ic_lora: [{ id: 'i1', lora: { model: 'x', strength: 1 }, ref_media: null, strength: 1 }] } },
			caps
		);
		expect(deriveDirectorMode(withIcLora, caps)).toBe('director');
	});

	it('chain routing: a bare single segment derives t2v', () => {
		const v = normalizeDirectorValue({ mode: 'director', chain: { segments: [{ id: 'c1', prompt: 'a' }] } }, wanCaps);
		expect(deriveDirectorMode(v, wanCaps)).toBe('t2v');
	});

	it('chain routing: a single segment with a leading keyframe derives i2v', () => {
		const v = normalizeDirectorValue(
			{ mode: 'director', chain: { segments: [{ id: 'c1', prompt: 'a', keyframe: media('/s.png') }] } },
			wanCaps
		);
		expect(deriveDirectorMode(v, wanCaps)).toBe('i2v');
	});

	it('chain routing: a leading + trailing keyframe on the same segment derives flf when enabled, director otherwise', () => {
		const raw = {
			mode: 'director' as const,
			chain: { segments: [{ id: 'c1', prompt: 'a', keyframe: media('/s.png'), last_keyframe: media('/e.png') }] }
		};
		expect(deriveDirectorMode(normalizeDirectorValue(raw, wanCaps), wanCaps)).toBe('flf');
		// HYBRID_RAW_CAPS enables director's keyframes/audio but never declares
		// an flf mode -- the same structural shape stays `director` there.
		expect(deriveDirectorMode(normalizeDirectorValue(raw, hybridCaps), hybridCaps)).toBe('director');
	});

	it('chain routing: more than one segment, or a placed keyframe/audio, is director', () => {
		const twoShots = normalizeDirectorValue(
			{ mode: 'director', chain: { segments: [{ id: 'c1', prompt: 'a' }, { id: 'c2', prompt: 'b' }] } },
			wanCaps
		);
		expect(deriveDirectorMode(twoShots, wanCaps)).toBe('director');

		const withKeyframe = normalizeDirectorValue(
			{
				mode: 'director',
				chain: { segments: [{ id: 'c1', prompt: 'a' }], keyframes: [{ id: 'k1', at: 1, strength: 1, media: media('/k.png') }] }
			},
			hybridCaps
		);
		expect(deriveDirectorMode(withKeyframe, hybridCaps)).toBe('director');

		const withAudio = normalizeDirectorValue(
			{
				mode: 'director',
				chain: {
					segments: [{ id: 'c1', prompt: 'a' }],
					audio: [{ id: 'a1', role: 'condition', start: 0, trim_start: 0, length: 2, media: media('/a.wav') }]
				}
			},
			hybridCaps
		);
		expect(deriveDirectorMode(withAudio, hybridCaps)).toBe('director');
	});
});

describe('toModelessDirectorValue', () => {
	const caps = parseDirectorCapabilities(RAW_CAPS)!; // timeline routing
	const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!; // chain routing

	it('is a structural no-op for an already-unified director document beyond marking ui.modeless', () => {
		const v = normalizeDirectorValue({ mode: 'director', global_prompt: 'a' }, caps);
		const projected = toModelessDirectorValue(v, caps);
		expect(projected.chain).toEqual(v.chain);
		expect(projected.timeline).toEqual(v.timeline);
		expect(projected.simple).toEqual(v.simple);
		expect(projected.ui?.modeless).toBe(true);
	});

	it('is idempotent: a second pass returns a structurally identical document', () => {
		const v = normalizeDirectorValue(
			{ mode: 'i2v', simple: { duration: 5, fps: 24, start_image: media('/s.png'), first_frame: null, last_frame: null } },
			caps
		);
		const once = toModelessDirectorValue(v, caps);
		const twice = toModelessDirectorValue(once, caps);
		expect(twice).toEqual(once);
	});

	it('idempotency does not resurrect media the modeless editor already cleared', () => {
		// Simulates VideoDirectorEditor.svelte's own re-normalize cycle: project
		// once (as a first load of a legacy document would), then clear the
		// leading keyframe the way Stage does, then re-project as the editor's
		// external-sync effect would on the next re-render -- the clear must
		// stick, not resurrect from the untouched (and by now stale)
		// `simple.start_image` the first projection read it from.
		const v = normalizeDirectorValue(
			{ mode: 'i2v', simple: { duration: 5, fps: 16, start_image: media('/s.png'), first_frame: null, last_frame: null } },
			wanCaps
		);
		const projected = toModelessDirectorValue(v, wanCaps);
		expect(projected.chain.segments[0].keyframe).toEqual(media('/s.png'));

		const cleared = {
			...projected,
			chain: { ...projected.chain, segments: [{ ...projected.chain.segments[0], keyframe: null }] }
		};
		const reprojected = toModelessDirectorValue(cleared, wanCaps);
		expect(reprojected.chain.segments[0].keyframe).toBeNull();
	});

	it('timeline t2v: the projected document derives back to t2v and submits byte-identically to the legacy path', () => {
		const legacy = { ...normalizeDirectorValue({ mode: 't2v' }, caps), global_prompt: 'a red car' };
		const projected = toModelessDirectorValue(legacy, caps);
		expect(deriveDirectorMode(projected, caps)).toBe('t2v');
		expect(JSON.stringify(buildDirectorSubmission(projected, caps))).toBe(JSON.stringify(buildDirectorSubmission(legacy, caps)));
	});

	it('timeline i2v: the projected document derives back to i2v and submits byte-identically to the legacy path', () => {
		const legacy = {
			...normalizeDirectorValue({ mode: 'i2v' }, caps),
			global_prompt: 'a red car',
			simple: { duration: 5, fps: 24, start_image: media('/s.png'), first_frame: null, last_frame: null }
		};
		const projected = toModelessDirectorValue(legacy, caps);
		expect(deriveDirectorMode(projected, caps)).toBe('i2v');
		expect(JSON.stringify(buildDirectorSubmission(projected, caps))).toBe(JSON.stringify(buildDirectorSubmission(legacy, caps)));
	});

	it('timeline flf: the projected document derives back to flf and submits byte-identically to the legacy path', () => {
		const legacy = {
			...normalizeDirectorValue({ mode: 'flf' }, caps),
			global_prompt: 'a red car',
			simple: { duration: 7, fps: 24, start_image: null, first_frame: media('/f.png'), last_frame: media('/l.png') }
		};
		const projected = toModelessDirectorValue(legacy, caps);
		expect(deriveDirectorMode(projected, caps)).toBe('flf');
		expect(JSON.stringify(buildDirectorSubmission(projected, caps))).toBe(JSON.stringify(buildDirectorSubmission(legacy, caps)));
	});

	it('chain t2v: the projected document derives back to t2v and submits byte-identically to the legacy path', () => {
		const legacy = { ...normalizeDirectorValue({ mode: 't2v' }, wanCaps), global_prompt: 'a red car' };
		const projected = toModelessDirectorValue(legacy, wanCaps);
		expect(deriveDirectorMode(projected, wanCaps)).toBe('t2v');
		expect(JSON.stringify(buildDirectorSubmission(projected, wanCaps))).toBe(JSON.stringify(buildDirectorSubmission(legacy, wanCaps)));
	});

	it('chain i2v: the projected document derives back to i2v and submits byte-identically to the legacy path', () => {
		const legacy = {
			...normalizeDirectorValue({ mode: 'i2v' }, wanCaps),
			global_prompt: 'a red car',
			simple: { duration: 5, fps: 16, start_image: media('/s.png'), first_frame: null, last_frame: null }
		};
		const projected = toModelessDirectorValue(legacy, wanCaps);
		expect(deriveDirectorMode(projected, wanCaps)).toBe('i2v');
		expect(JSON.stringify(buildDirectorSubmission(projected, wanCaps))).toBe(JSON.stringify(buildDirectorSubmission(legacy, wanCaps)));
	});

	it('chain flf: the projected document derives back to flf and submits byte-identically to the legacy path', () => {
		const legacy = {
			...normalizeDirectorValue({ mode: 'flf' }, wanCaps),
			global_prompt: 'a red car',
			simple: { duration: 7, fps: 16, start_image: null, first_frame: media('/f.png'), last_frame: media('/l.png') }
		};
		const projected = toModelessDirectorValue(legacy, wanCaps);
		expect(deriveDirectorMode(projected, wanCaps)).toBe('flf');
		// The trailing edge lands on segment 0's own `last_keyframe` field, the
		// same as the leading edge lands on `keyframe` -- `simple.*` is cleared
		// once projected, not left holding the live value.
		expect(projected.chain.segments[0].last_keyframe).toEqual(media('/l.png'));
		expect(projected.simple.last_frame).toBeNull();
		expect(JSON.stringify(buildDirectorSubmission(projected, wanCaps))).toBe(JSON.stringify(buildDirectorSubmission(legacy, wanCaps)));
	});
});

describe('validateDirector parity on modeless (chain/timeline-shaped) simple documents', () => {
	// validateDirector projects internally now (toModelessDirectorValue), so it
	// must reach the same verdict whether it's handed a legacy simple-shaped
	// value or the same value pre-projected by the editor.
	const caps = parseDirectorCapabilities(RAW_CAPS)!;
	const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!;

	it('timeline i2v: legacy and pre-projected values validate identically, both ok once prompt and image are set', () => {
		const legacy = {
			...normalizeDirectorValue({ mode: 'i2v' }, caps),
			simple: { duration: 5, fps: 24, start_image: media('/s.png'), first_frame: null, last_frame: null }
		};
		const projected = toModelessDirectorValue(legacy, caps);
		expect(validateDirector(projected, caps)).toEqual(validateDirector(legacy, caps));
		expect(validateDirector(projected, caps)).toEqual({ ok: false, reasons: ['Missing prompt'] });

		const withPrompt = { ...projected, global_prompt: 'p' };
		expect(validateDirector(withPrompt, caps).ok).toBe(true);
	});

	it('chain i2v: legacy and pre-projected values validate identically', () => {
		const legacy = {
			...normalizeDirectorValue({ mode: 'i2v' }, wanCaps),
			global_prompt: 'p',
			simple: { duration: 5, fps: 16, start_image: media('/s.png'), first_frame: null, last_frame: null }
		};
		const projected = toModelessDirectorValue(legacy, wanCaps);
		expect(validateDirector(projected, wanCaps)).toEqual(validateDirector(legacy, wanCaps));
		expect(validateDirector(projected, wanCaps).ok).toBe(true);
	});
});

describe('mode coherence after an edit', () => {
	const caps = parseDirectorCapabilities(RAW_CAPS)!; // timeline routing
	const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!; // chain routing

	it('a chat-driven set_mode + upsert_media sequence produces a document whose derived mode agrees with the mode it set', () => {
		const v = toModelessDirectorValue(normalizeDirectorValue({ mode: 't2v' }, caps), caps);
		expect(deriveDirectorMode(v, caps)).toBe('t2v');

		const edited = applyDirectorOperations(
			v,
			[{ op: 'set_mode', mode: 'i2v' }, { op: 'upsert_media', media: { id: 'm1', role: 'first', path: '/s.png' } }],
			caps
		);
		expect(deriveDirectorMode(edited, caps)).toBe('i2v');
		expect(edited.mode).toBe('i2v');
	});

	it('removing the leading frame again derives back to t2v even while the stored mode field still says i2v', () => {
		const v = toModelessDirectorValue(
			normalizeDirectorValue(
				{ mode: 'i2v', simple: { duration: 5, fps: 24, start_image: media('/s.png'), first_frame: null, last_frame: null } },
				caps
			),
			caps
		);
		expect(deriveDirectorMode(v, caps)).toBe('i2v');

		// Removes the projected timeline keyframe (see toModelessDirectorValue --
		// the leading edge for a fresh i2v projection always mints id 'kf-first').
		const cleared = applyDirectorOperations(v, [{ op: 'remove_media', id: 'kf-first' }], caps);
		expect(deriveDirectorMode(cleared, caps)).toBe('t2v');
		// The stored field only catches up once something re-derives and writes
		// it back (VideoDirectorEditor.svelte's own coherence effect) -- proving
		// deriveDirectorMode, not `.mode`, is the actual source of truth.
		expect(cleared.mode).toBe('i2v');
	});

	it('the chain-routed leading well flips the derived mode the same way', () => {
		const v = toModelessDirectorValue(normalizeDirectorValue({ mode: 't2v' }, wanCaps), wanCaps);
		expect(deriveDirectorMode(v, wanCaps)).toBe('t2v');

		const edited = applyDirectorOperations(
			v,
			[{ op: 'set_mode', mode: 'i2v' }, { op: 'upsert_media', media: { id: 'm1', role: 'first', path: '/s.png' } }],
			wanCaps
		);
		expect(deriveDirectorMode(edited, wanCaps)).toBe('i2v');
	});
});

// ─── MiniMax-H3 refs mode: preset_mode_overrides + the `references` capability ──

// A chain-routed director base (like Wan), plus a 'refs' preset_mode override
// that swaps its keyframe well off and turns on a per-shot pick from the
// whole-form reference pool -- mirrors the shape H3's preset will declare.
// Mirrors presets/native/MiniMax-H3/preset.yml's `vars.video_director` block
// exactly (byte-for-byte structure): a chain-routed director base (`video`
// mode) whose `refs` preset_mode override turns off keyframes/audio/
// continuation and turns on a per-shot pick from a three-field reference pool.
const H3_RAW_CAPS = {
	preset_modes: ['video', 'refs'],
	segment_routing: true,
	modes: {
		t2v: {},
		i2v: {},
		flf: {},
		director: {
			keyframes: 'anywhere',
			audio: true,
			max_keyframes: 8,
			max_segments: 6,
			max_frames_per_segment: 345,
			continuation: { source: 'tail_frames', overlap_frames: 17, stitch: true },
			max_overlap_frames: 34
		}
	},
	limits: { default_duration: 5, default_fps: 24, max_duration: 15 },
	preset_mode_overrides: {
		refs: {
			references: 'per_shot',
			reference_fields: ['references', 'reference_videos', 'reference_audios'],
			modes: {
				director: {
					keyframes: null,
					audio: false,
					continuation: null,
					max_overlap_frames: null
				}
			}
		}
	}
};

describe('seedDirectorPromptFromLegacyText', () => {
	const refsCaps = resolveDirectorCapabilities(H3_RAW_CAPS, 'refs')!;

	// The bug itself: a preset mode that only just gained Director capability
	// (H3's `refs`, commit 1b16939b) leaves a tab's existing plain-prompt text
	// completely invisible to the fresh document the editor mounts with -- the
	// gate `+page.svelte` uses fails even though the user very much has a
	// prompt, just not one the Director document has ever heard of.
	it('reproduces the dead-Generate bug: a fresh director document ignores an existing legacy prompt', () => {
		const doc = normalizeDirectorValue(undefined, refsCaps);
		const result = validateDirector(doc, refsCaps);
		// A single, unadorned chain segment projects to `t2v` (deriveDirectorMode),
		// so the reported reason is that mode's, not the routed chain's -- either
		// way, the document is unusable despite a legacy prompt existing elsewhere.
		expect(result).toEqual({ ok: false, reasons: ['Missing prompt'] });
	});

	it('migrates the legacy prompt into the first chain segment of a still-default document', () => {
		const seeded = seedDirectorPromptFromLegacyText(undefined, refsCaps, 'a cat walking through neon rain');
		expect(seeded).not.toBeNull();
		expect(seeded!.chain.segments[0].prompt).toBe('a cat walking through neon rain');
		expect(seeded!.chain.segments[0].prompt_segments).toEqual([
			expect.objectContaining({ content: 'a cat walking through neon rain' })
		]);
		expect(validateDirector(seeded!, refsCaps)).toEqual({ ok: true, reasons: [] });
	});

	it('migrates into the single timeline segment for a non-routed (LTX-style) director', () => {
		const timelineCaps = parseDirectorCapabilities(RAW_CAPS)!;
		const seeded = seedDirectorPromptFromLegacyText(undefined, timelineCaps, 'legacy timeline prompt');
		expect(seeded).not.toBeNull();
		expect(seeded!.timeline.segments).toHaveLength(1);
		expect(seeded!.timeline.segments[0].text).toBe('legacy timeline prompt');
		expect(validateDirector(seeded!, timelineCaps).ok).toBe(true);
	});

	it('is a no-op for blank or whitespace-only legacy text', () => {
		expect(seedDirectorPromptFromLegacyText(undefined, refsCaps, '')).toBeNull();
		expect(seedDirectorPromptFromLegacyText(undefined, refsCaps, '   ')).toBeNull();
	});

	it('never resurrects text onto a document the user has already started shaping', () => {
		const touched = applySetPrompt(createDefaultDirectorValue(refsCaps), 'the user already typed this');
		expect(seedDirectorPromptFromLegacyText(touched, refsCaps, 'legacy text')).toBeNull();
	});

	it('is self-gating: seeding twice never re-fires (the first write makes the document non-default)', () => {
		const first = seedDirectorPromptFromLegacyText(undefined, refsCaps, 'legacy prompt');
		expect(first).not.toBeNull();
		const second = seedDirectorPromptFromLegacyText(first!, refsCaps, 'legacy prompt');
		expect(second).toBeNull();
	});
});

describe('resolveDirectorCapabilities', () => {
	it('matches parseDirectorCapabilities when presetMode is null/undefined', () => {
		const base = parseDirectorCapabilities(H3_RAW_CAPS);
		expect(resolveDirectorCapabilities(H3_RAW_CAPS, null)).toEqual(base);
		expect(resolveDirectorCapabilities(H3_RAW_CAPS, undefined)).toEqual(base);
	});

	it('matches parseDirectorCapabilities when presetMode names no override', () => {
		expect(resolveDirectorCapabilities(H3_RAW_CAPS, 'video')).toEqual(parseDirectorCapabilities(H3_RAW_CAPS));
	});

	it('`references`/`reference_fields` are TOP-LEVEL capabilities, not per composition mode', () => {
		const resolved = resolveDirectorCapabilities(H3_RAW_CAPS, 'refs')!;
		expect(resolved.references).toBe('per_shot');
		expect(resolved.referenceFields).toEqual(['references', 'reference_videos', 'reference_audios']);
		expect(parseDirectorCapabilities(H3_RAW_CAPS)!.references).toBeNull();
	});

	it('a mode named in override.modes is shallow-merged onto the base entry -- fields it never names keep the base value', () => {
		const resolved = resolveDirectorCapabilities(H3_RAW_CAPS, 'refs')!;
		expect(resolved.modes.director?.keyframes).toBe('none'); // explicit `null` coerces the same as absent
		expect(resolved.modes.director?.audio).toBe(false);
		expect(resolved.modes.director?.maxOverlapFrames).toBeNull();
		// Untouched by the override -- still the base's value.
		expect(resolved.modes.director?.maxSegments).toBe(6);
		expect(resolved.modes.director?.maxFramesPerSegment).toBe(345);
	});

	it('`continuation` EXPLICITLY null in the override sets continuationDisabled -- absence of the key does not', () => {
		expect(parseDirectorCapabilities(H3_RAW_CAPS)!.modes.director?.continuationDisabled).toBe(false);
		const resolved = resolveDirectorCapabilities(H3_RAW_CAPS, 'refs')!;
		expect(resolved.modes.director?.continuation).toBeNull();
		expect(resolved.modes.director?.continuationDisabled).toBe(true);
	});

	it('leaves modes the override does not name untouched', () => {
		const resolved = resolveDirectorCapabilities(H3_RAW_CAPS, 'refs')!;
		expect(resolved.modes.t2v).toEqual(parseDirectorCapabilities(H3_RAW_CAPS)!.modes.t2v);
	});

	it('presetModes is read from the base, unaffected by the override', () => {
		expect(resolveDirectorCapabilities(H3_RAW_CAPS, 'refs')!.presetModes).toEqual(['video', 'refs']);
	});

	it('a `limits` override REPLACES the base limits wholesale (not field-merged) -- mirrors apply_preset_mode_overlay', () => {
		const raw = { ...H3_RAW_CAPS, preset_mode_overrides: { refs: { limits: { max_duration: 12 } } } };
		const resolved = resolveDirectorCapabilities(raw, 'refs')!;
		expect(resolved.maxDuration).toBe(12);
		// default_duration/default_fps were NOT repeated in the override's
		// `limits`, so they fall back to parseDirectorCapabilities' OWN defaults
		// (5, 24) for a fresh limits object -- not the base's declared 5/24
		// coincidentally matching here would hide the bug; the base's `limits`
		// (default_fps 24) already matches parseDirectorCapabilities' default,
		// so this only proves it via defaultDuration too.
		expect(resolved.defaultDuration).toBe(5);
	});

	it('an override can enable a mode the base never declared', () => {
		const raw = {
			preset_modes: ['video', 'refs'],
			modes: { t2v: {} },
			preset_mode_overrides: { refs: { modes: { flf: { audio: true } } } }
		};
		expect(resolveDirectorCapabilities(raw, 'video')!.enabledModes).toEqual(['t2v']);
		const resolved = resolveDirectorCapabilities(raw, 'refs')!;
		expect(resolved.enabledModes).toEqual(['t2v', 'flf']);
		expect(resolved.modes.flf?.audio).toBe(true);
	});
});

describe('buildDirectorSubmission: per-shot references', () => {
	const refsCaps = resolveDirectorCapabilities(H3_RAW_CAPS, 'refs')!; // chain routing, references = 'per_shot'
	const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!; // chain routing, references capability null

	// Two segments so the document derives to 'director' rather than a bare
	// t2v/i2v single shot (buildDirectorSubmission's single-shot branches never
	// read `.references` at all -- see the neutrality tests below for why a
	// lone segment must NOT be forced into 'director' by carrying one).
	function twoShotChainDoc(first: Partial<ChainSegment>, c: NonNullable<ReturnType<typeof resolveDirectorCapabilities>>): VideoDirectorValue {
		const base = normalizeDirectorValue({ mode: 'director' }, c);
		return { ...base, chain: { ...base.chain, segments: [chainSegment({ id: 'c1', ...first }), chainSegment({ id: 'c2' })] } };
	}

	it('emits a resolved-path selection as {path}', () => {
		const doc = twoShotChainDoc({ references: [{ path: '/ref1.png' }] }, refsCaps);
		const wire = buildDirectorSubmission(doc, refsCaps);
		expect(wire.segments[0].references).toEqual([{ path: '/ref1.png' }]);
		expect(wire.segments[1].references).toBeUndefined();
	});

	it('emits a form-pool selection as {form_media: {field, path}} verbatim -- no path resolution at build time', () => {
		const doc = twoShotChainDoc({ references: [{ form_media: { field: 'references', path: '/pool/a.png' } }] }, refsCaps);
		const wire = buildDirectorSubmission(doc, refsCaps);
		expect(wire.segments[0].references).toEqual([{ form_media: { field: 'references', path: '/pool/a.png' } }]);
	});

	it('omits references on a shot with no explicit selection -- absent means the whole pool', () => {
		const doc = twoShotChainDoc({}, refsCaps);
		const wire = buildDirectorSubmission(doc, refsCaps);
		expect(wire.segments[0].references).toBeUndefined();
	});

	it('never emits references when the capability is null', () => {
		const doc = twoShotChainDoc({ references: [{ path: '/ref1.png' }] }, wanCaps);
		const wire = buildDirectorSubmission(doc, wanCaps);
		expect(wire.segments[0].references).toBeUndefined();
	});

	it('never emits settings.continuation when continuation is disabled -- the backend hard-rejects it', () => {
		const doc = twoShotChainDoc({}, refsCaps);
		const wire = buildDirectorSubmission(doc, refsCaps);
		expect(wire.settings).not.toHaveProperty('continuation');
	});

	it('a non-refs chain mode still emits settings.continuation as before', () => {
		const doc = twoShotChainDoc({}, wanCaps);
		const wire = buildDirectorSubmission(doc, wanCaps);
		expect(wire.settings).toHaveProperty('continuation');
	});

	it('timeline-routed director also emits per-shot references', () => {
		const timelineRefsCaps = parseDirectorCapabilities({
			...RAW_CAPS,
			references: 'per_shot',
			reference_fields: ['references']
		})!;
		const doc = normalizeDirectorValue(
			{
				mode: 'director',
				timeline: {
					duration: 10,
					fps: 24,
					segments: [
						{ id: 's1', start: 0, end: 5, text: 'a', prompt_segments: [], references: [{ path: '/r.png' }] },
						{ id: 's2', start: 5, end: 10, text: 'b', prompt_segments: [] }
					],
					keyframes: [],
					audio: [],
					ic_lora: []
				}
			},
			timelineRefsCaps
		);
		const wire = buildDirectorSubmission(doc, timelineRefsCaps);
		expect(wire.segments[0].references).toEqual([{ path: '/r.png' }]);
		expect(wire.segments[1].references).toBeUndefined();
	});
});

describe('validateDirector: per-shot references', () => {
	const refsCaps = resolveDirectorCapabilities(H3_RAW_CAPS, 'refs')!;
	const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!; // no references capability at all

	function twoShotChainDoc(first: Partial<ChainSegment>, c: NonNullable<ReturnType<typeof resolveDirectorCapabilities>>): VideoDirectorValue {
		const base = normalizeDirectorValue({ mode: 'director' }, c);
		return { ...base, chain: { ...base.chain, segments: [chainSegment({ id: 'c1', ...first }), chainSegment({ id: 'c2' })] } };
	}

	it('a selection against a declared reference field is valid', () => {
		const doc = twoShotChainDoc({ references: [{ form_media: { field: 'references', path: '/a.png' } }] }, refsCaps);
		expect(validateDirector(doc, refsCaps).ok).toBe(true);
	});

	it('a selection against an undeclared field is rejected', () => {
		const doc = twoShotChainDoc({ references: [{ form_media: { field: 'not_a_reference_field', path: '/a.png' } }] }, refsCaps);
		const result = validateDirector(doc, refsCaps);
		expect(result.ok).toBe(false);
		expect(result.reasons).toContain("A per-shot reference points at a field this mode doesn't declare as a reference field");
	});

	it('no selection at all is valid under per_shot -- absent means All, not missing', () => {
		const doc = twoShotChainDoc({}, refsCaps);
		expect(validateDirector(doc, refsCaps).ok).toBe(true);
	});

	it('a stored selection is rejected once the mode has no reference pool at all', () => {
		const doc = twoShotChainDoc({ references: [{ path: '/a.png' }] }, wanCaps);
		const result = validateDirector(doc, wanCaps);
		expect(result.ok).toBe(false);
		expect(result.reasons).toContain('Per-shot references are not supported in this mode');
	});
});

// `references` is a TOP-LEVEL capability (like segment_routing), not scoped to
// the 'director' composition mode -- a single-shot t2v/i2v/flf document reads
// and emits it exactly like a multi-shot chain/timeline segment does.
describe('references on single-shot t2v/i2v/flf documents', () => {
	const refsCaps = resolveDirectorCapabilities(H3_RAW_CAPS, 'refs')!; // segmentRouting, references = 'per_shot'
	const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!; // no reference pool at all

	function oneShotChainDoc(overrides: Partial<ChainSegment>, c: NonNullable<ReturnType<typeof resolveDirectorCapabilities>> = refsCaps): VideoDirectorValue {
		const base = normalizeDirectorValue({ mode: 'director' }, c);
		return { ...base, chain: { ...base.chain, segments: [chainSegment({ id: 'c1', ...overrides })] } };
	}

	it('a t2v single shot (no edge media) still emits its per-shot selection on the wire', () => {
		const doc = oneShotChainDoc({ references: [{ path: '/ref.png' }] });
		expect(deriveDirectorMode(doc, refsCaps)).toBe('t2v');
		const wire = buildDirectorSubmission(doc, refsCaps);
		expect(wire.mode).toBe('t2v');
		expect(wire.segments[0].references).toEqual([{ path: '/ref.png' }]);
	});

	it('an i2v single shot (leading keyframe + references) emits both', () => {
		const doc = oneShotChainDoc({ keyframe: media('/start.png'), references: [{ path: '/ref.png' }] });
		expect(deriveDirectorMode(doc, refsCaps)).toBe('i2v');
		const wire = buildDirectorSubmission(doc, refsCaps);
		expect(wire.mode).toBe('i2v');
		expect(wire.segments[0].references).toEqual([{ path: '/ref.png' }]);
	});

	it('validateDirector rejects a single-shot selection against an undeclared field', () => {
		const doc = oneShotChainDoc({ references: [{ form_media: { field: 'not_a_reference_field', path: '/a.png' } }] });
		const result = validateDirector(doc, refsCaps);
		expect(result.ok).toBe(false);
		expect(result.reasons).toContain("A per-shot reference points at a field this mode doesn't declare as a reference field");
	});

	it('validateDirector rejects a single-shot selection once the mode has no reference pool at all', () => {
		const doc = oneShotChainDoc({ references: [{ path: '/a.png' }] }, wanCaps);
		const result = validateDirector(doc, wanCaps);
		expect(result.ok).toBe(false);
		expect(result.reasons).toContain('Per-shot references are not supported in this mode');
	});

	it('timeline-routed t2v single shot also emits references', () => {
		const timelineRefsCaps = parseDirectorCapabilities({ ...RAW_CAPS, references: 'per_shot', reference_fields: ['references'] })!;
		const doc = normalizeDirectorValue(
			{
				mode: 'director',
				timeline: {
					duration: 5,
					fps: 24,
					segments: [{ id: 's1', start: 0, end: 5, text: '', prompt_segments: [], references: [{ path: '/r.png' }] }],
					keyframes: [],
					audio: [],
					ic_lora: []
				},
				global_prompt: 'a shot'
			},
			timelineRefsCaps
		);
		expect(deriveDirectorMode(doc, timelineRefsCaps)).toBe('t2v');
		const wire = buildDirectorSubmission(doc, timelineRefsCaps);
		expect(wire.mode).toBe('t2v');
		expect(wire.segments[0].references).toEqual([{ path: '/r.png' }]);
	});
});

describe('applyDirectorOperations: upsert_segment references', () => {
	const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!; // chain routing
	const ltxCaps = parseDirectorCapabilities(RAW_CAPS)!; // timeline routing

	function chainDoc(segment: ChainSegment, c = wanCaps): VideoDirectorValue {
		const base = normalizeDirectorValue({ mode: 'director' }, c);
		return { ...base, chain: { ...base.chain, segments: [segment] } };
	}

	it('a resolved-path reference round-trips onto a chain segment', () => {
		const doc = chainDoc(chainSegment({ id: 'c1' }));
		const next = applyDirectorOperations(doc, [{ op: 'upsert_segment', segment: { id: 'c1', references: [{ path: '/pool/a.png' }] } }], wanCaps);
		expect(next.chain.segments[0].references).toEqual([{ path: '/pool/a.png' }]);
	});

	it('a form_media-addressed reference round-trips verbatim -- get_video_director reads it straight off the document', () => {
		const doc = chainDoc(chainSegment({ id: 'c1' }));
		const next = applyDirectorOperations(
			doc,
			[{ op: 'upsert_segment', segment: { id: 'c1', references: [{ form_media: { field: 'references', path: '/pool/a.png' } }] } }],
			wanCaps
		);
		expect(next.chain.segments[0].references).toEqual([{ form_media: { field: 'references', path: '/pool/a.png' } }]);
	});

	it('an empty references array clears the selection back to "All" (undefined), not []', () => {
		const doc = chainDoc(chainSegment({ id: 'c1', references: [{ path: '/pool/a.png' }] }));
		const next = applyDirectorOperations(doc, [{ op: 'upsert_segment', segment: { id: 'c1', references: [] } }], wanCaps);
		expect(next.chain.segments[0].references).toBeUndefined();
	});

	it('omitting `references` from the op leaves an existing selection untouched', () => {
		const doc = chainDoc(chainSegment({ id: 'c1', references: [{ path: '/pool/a.png' }] }));
		const next = applyDirectorOperations(doc, [{ op: 'upsert_segment', segment: { id: 'c1', prompt: 'new prompt' } }], wanCaps);
		expect(next.chain.segments[0].references).toEqual([{ path: '/pool/a.png' }]);
	});

	it('round-trips on a timeline-routed segment too', () => {
		const doc = normalizeDirectorValue(
			{ mode: 'director', timeline: { duration: 5, fps: 24, segments: [{ id: 's1', start: 0, end: 5, text: 'x', prompt_segments: [] }], keyframes: [], audio: [], ic_lora: [] } },
			ltxCaps
		);
		const next = applyDirectorOperations(doc, [{ op: 'upsert_segment', segment: { id: 's1', references: [{ path: '/r.png' }] } }], ltxCaps);
		expect(next.timeline.segments[0].references).toEqual([{ path: '/r.png' }]);
	});
});

describe('deriveDirectorMode / buildDirectorSubmission: references is conditioning, not structure', () => {
	const refsCaps = resolveDirectorCapabilities(H3_RAW_CAPS, 'refs')!;

	it('a lone segment carrying a per-shot reference selection still derives t2v (references is conditioning, not structure -- but IS still emitted, since the capability is top-level, not director-only)', () => {
		const base = normalizeDirectorValue({ mode: 'director' }, refsCaps);
		const doc = { ...base, chain: { ...base.chain, segments: [chainSegment({ id: 'c1', references: [{ path: '/ref.png' }] })] } };
		expect(deriveDirectorMode(doc, refsCaps)).toBe('t2v');
		expect(buildDirectorSubmission(doc, refsCaps).segments[0].references).toEqual([{ path: '/ref.png' }]);
	});

	it('a lone segment with a leading keyframe AND references still derives i2v, not director', () => {
		const base = normalizeDirectorValue({ mode: 'director' }, refsCaps);
		const doc = {
			...base,
			chain: {
				...base.chain,
				segments: [chainSegment({ id: 'c1', keyframe: media('/start.png'), references: [{ path: '/ref.png' }] })]
			}
		};
		expect(deriveDirectorMode(doc, refsCaps)).toBe('i2v');
	});
});
