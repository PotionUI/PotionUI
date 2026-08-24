import { describe, it, expect } from 'vitest';
import {
	deriveRailModel,
	resolveKeyframeDrag,
	resizeTimelineBlockEdge,
	deriveShotLabel,
	isKeyframeLocked,
	withChainKeyframeAt,
	withTimelineKeyframeAt,
	withTimelineSegmentEdge
} from './railModel';
import { chainEdgeKeyframeId, resolveDirectorCapabilities } from '$lib/utils/videoDirector';
import { withAddedShot, withChainLeadingMedia, withChainTrailingMedia } from './stageModel';
import type { VideoDirectorValue, DirectorCapabilities, DirectorModeCapability, ChainSegment } from '$lib/types/videoDirector';

function baseModeCap(overrides: Partial<DirectorModeCapability> = {}): DirectorModeCapability {
	return {
		tips: [],
		maxDuration: null,
		audio: false,
		icLora: false,
		maxKeyframes: null,
		perSegmentLoras: false,
		keyframes: 'none',
		maxSegments: null,
		maxFramesPerSegment: null,
		defaultSegmentDuration: 5,
		continuation: null,
		maxOverlapFrames: null,
		continuationDisabled: false,
		...overrides
	};
}

function baseDoc(): VideoDirectorValue {
	return {
		schema_version: 1,
		mode: 'director',
		global_prompt: '',
		global_prompt_segments: [],
		negative_prompt: '',
		negative_prompt_segments: [],
		simple: { duration: 5, fps: 24, start_image: null, first_frame: null, last_frame: null },
		timeline: { duration: 5, fps: 24, segments: [], keyframes: [], audio: [], ic_lora: [] },
		chain: {
			fps: 16,
			segments: [],
			continuation: { overlap_frames: 0, stitch: true },
			keyframes: [],
			audio: []
		}
	};
}

function chainSegment(id: string, prompt: string, duration: number, override: 't2v' | null = null): ChainSegment {
	return {
		id,
		prompt,
		prompt_segments: [],
		duration,
		loras: null,
		keyframe: null,
		keyframe_strength: 1,
		last_keyframe: null,
		last_keyframe_strength: 1,
		sub_type_override: override
	};
}

// ─── Wan chain fixture: 49 + 81 + 33 frames @16fps, one 16f continue overlap,
// second join a hard cut. Matches the design brief's worked example. ───

function wanCaps(): DirectorCapabilities {
	return {
		presetModes: null,
		modes: {
			director: baseModeCap({
				perSegmentLoras: true,
				keyframes: 'first_only',
				maxSegments: 8,
				maxFramesPerSegment: 81,
				continuation: { source: 'tail_frames', overlapFrames: 16, stitch: true },
				maxOverlapFrames: 81
			})
		},
		enabledModes: ['director'],
		defaultDuration: 5,
		defaultFps: 16,
		maxDuration: null,
		maxFrames: null,
		segmentRouting: true,
		references: null,
		referenceFields: []
	};
}

function wanDoc(): VideoDirectorValue {
	const doc = baseDoc();
	doc.chain.fps = 16;
	doc.chain.continuation = { overlap_frames: 16, stitch: true };
	doc.chain.segments = [
		chainSegment('s1', 'Lanterns, rain on paper lanterns', 49 / 16),
		chainSegment('s2', 'Steam swallows the frame', 81 / 16),
		chainSegment('s3', 'Across the street', 33 / 16, 't2v')
	];
	return doc;
}

// ─── MiniMax-H3 chain fixture: 145 + 201 + 105 frames @25fps, two 17f
// continue overlaps, keyframes anywhere + audio. ───

function h3Caps(): DirectorCapabilities {
	return {
		presetModes: null,
		modes: {
			director: baseModeCap({
				audio: true,
				maxKeyframes: 8,
				keyframes: 'anywhere',
				maxSegments: 6,
				maxFramesPerSegment: 345,
				continuation: { source: 'tail_frames', overlapFrames: 17, stitch: true },
				maxOverlapFrames: 17
			})
		},
		enabledModes: ['director'],
		defaultDuration: 5,
		defaultFps: 25,
		maxDuration: null,
		maxFrames: null,
		segmentRouting: true,
		references: null,
		referenceFields: []
	};
}

function h3Doc(): VideoDirectorValue {
	const doc = baseDoc();
	doc.chain.fps = 25;
	doc.chain.continuation = { overlap_frames: 17, stitch: true };
	doc.chain.segments = [
		chainSegment('s1', 'Stall row', 145 / 25),
		chainSegment('s2', 'The wok', 201 / 25),
		chainSegment('s3', 'Looking up', 105 / 25)
	];
	return doc;
}

// ─── LTX timeline fixture: three non-overlapping prompt blocks @25fps
// covering 297 of the 1001-frame generator cap. ───

function ltxCaps(): DirectorCapabilities {
	return {
		presetModes: null,
		modes: {
			director: baseModeCap({
				audio: true,
				icLora: true,
				maxKeyframes: null,
				keyframes: 'anywhere'
			})
		},
		enabledModes: ['director'],
		defaultDuration: 5,
		defaultFps: 25,
		maxDuration: 40.04,
		maxFrames: 1001,
		segmentRouting: false,
		references: null,
		referenceFields: []
	};
}

function ltxDoc(): VideoDirectorValue {
	const doc = baseDoc();
	doc.timeline.fps = 25;
	doc.timeline.duration = 40.04;
	doc.timeline.segments = [
		{ id: 'b1', start: 0, end: 4.2, text: 'Rain on the neon sign', prompt_segments: [] },
		{ id: 'b2', start: 4.2, end: 8.6, text: 'Past the noodle window', prompt_segments: [] },
		{ id: 'b3', start: 8.6, end: 11.88, text: 'The sign goes out', prompt_segments: [] }
	];
	return doc;
}

describe('deriveRailModel — Wan chain', () => {
	it('contributes 147 frames / 9.19s from 49+81+33 with one 16f continue overlap', () => {
		const model = deriveRailModel(wanDoc(), wanCaps());
		expect(model.routing).toBe('chain');
		expect(model.shots.map((s) => s.contributedFrames)).toEqual([49, 65, 33]);
		expect(model.shots.map((s) => s.totalFrames)).toEqual([49, 81, 33]);
		expect(model.totalFrames).toBe(147);
		expect(model.totalSeconds).toBeCloseTo(9.19, 2);
	});

	it('marks the first join continue (with an overlap shoulder) and the second a cut', () => {
		const model = deriveRailModel(wanDoc(), wanCaps());
		expect(model.seams).toHaveLength(2);
		expect(model.seams[0].kind).toBe('continue');
		expect(model.seams[0].overlapFrames).toBe(16);
		expect(model.seams[0].atFrame).toBe(49);
		expect(model.seams[0].shoulderStartFrame).toBe(33);
		expect(model.seams[1].kind).toBe('cut');
		expect(model.seams[1].overlapFrames).toBe(0);
		expect(model.seams[1].shoulderStartFrame).toBeNull();
	});

	it('gates lanes on capability: first_only draws no keyframes/audio lane', () => {
		const model = deriveRailModel(wanDoc(), wanCaps());
		expect(model.lanes).toEqual({ shots: true, keyframes: false, audio: false, icLora: false, references: false });
		expect(model.keyframes).toEqual([]);
		expect(model.audio).toEqual([]);
		expect(model.icLora).toBeNull();
	});

	it('chain routing stays gated on `keyframes` (unlike timeline): an undeclared keyframes field draws no lane either', () => {
		// The timeline fix below ("real LTX preset shape") only applies to
		// timeline routing -- a chain-style director with no `keyframes` field
		// (parses to 'none', same as Wan's own default before it sets
		// first_only) must still get no lane at all.
		const noKeyframesCaps: DirectorCapabilities = { ...wanCaps(), modes: { director: baseModeCap({ perSegmentLoras: true, maxSegments: 8 }) } };
		expect(noKeyframesCaps.modes.director!.keyframes).toBe('none');
		const model = deriveRailModel(wanDoc(), noKeyframesCaps);
		expect(model.lanes.keyframes).toBe(false);
	});
});

describe('deriveRailModel — MiniMax-H3 chain', () => {
	it('contributes 417 frames / 16.68s from 145+201+105 minus two 17f overlaps', () => {
		const model = deriveRailModel(h3Doc(), h3Caps());
		expect(model.shots.map((s) => s.contributedFrames)).toEqual([145, 184, 88]);
		expect(model.totalFrames).toBe(417);
		expect(model.totalSeconds).toBeCloseTo(16.68, 2);
	});

	it('draws the keyframes and audio lanes', () => {
		const model = deriveRailModel(h3Doc(), h3Caps());
		expect(model.lanes).toEqual({ shots: true, keyframes: true, audio: true, icLora: false, references: false });
		expect(model.maxKeyframes).toBe(8);
	});

	it('snap targets are exactly the shot boundaries (start, both joins, end)', () => {
		const model = deriveRailModel(h3Doc(), h3Caps());
		expect(model.snapTargets.map((t) => t.label)).toEqual(['Start', 'Join 1→2', 'Join 2→3', 'End']);
		expect(model.snapTargets.map((t) => Number(t.atSeconds.toFixed(2)))).toEqual([0, 5.8, 13.16, 16.68]);
	});

	it('reports a free (unsnapped) keyframe placed away from any boundary, and a snapped one', () => {
		const doc = h3Doc();
		doc.chain.keyframes = [
			{ id: 'kf-1', at: 11.24, strength: 1, media: { path: 'her-face.png' } },
			{ id: 'kf-2', at: 5.8, strength: 1, media: { path: 'other.png' } }
		];
		const model = deriveRailModel(doc, h3Caps());
		const free = model.keyframes.find((k) => k.id === 'kf-1')!;
		const snapped = model.keyframes.find((k) => k.id === 'kf-2')!;
		expect(free.snapped).toBe(false);
		expect(free.snappedToLabel).toBeNull();
		expect(snapped.snapped).toBe(true);
		expect(snapped.snappedToLabel).toBe('Join 1→2');
	});

	it('clamps a dragged free keyframe into [0, window] and snaps it near a boundary', () => {
		const model = deriveRailModel(h3Doc(), h3Caps());
		const past = resolveKeyframeDrag(999, model.snapTargets, model.totalSeconds);
		expect(past).toBeCloseTo(model.totalSeconds, 5);
		const negative = resolveKeyframeDrag(-5, model.snapTargets, model.totalSeconds);
		expect(negative).toBe(0);
		const nearJoin = resolveKeyframeDrag(5.83, model.snapTargets, model.totalSeconds);
		expect(nearJoin).toBeCloseTo(5.8, 5);
	});

	it('keeps an audio clip that spans two shots exactly as authored', () => {
		const doc = h3Doc();
		doc.chain.audio = [{ id: 'a1', start: 4, trim_start: 0, length: 6, media: { path: 'x.wav' }, role: 'mux' }];
		const model = deriveRailModel(doc, h3Caps());
		expect(model.audio).toHaveLength(1);
		expect(model.audio[0]).toMatchObject({ startSeconds: 4, endSeconds: 10, role: 'mux' });
		// crosses the 5.8s shot-1/shot-2 boundary and is left untouched
		expect(model.audio[0].startSeconds).toBeLessThan(5.8);
		expect(model.audio[0].endSeconds).toBeGreaterThan(5.8);
	});

	it('flags a shot over its per-segment cap with the wall positioned inside the block', () => {
		const doc = h3Doc();
		// Shot 2 dragged from 8.04s (201f) to 6.06s of *raw* generated length is
		// the wrong direction for this profile's cap (345f) to bite -- reuse
		// the Wan-scale numbers from the design brief's own over-cap artboard
		// instead: 97 raw frames against an 81f cap, 16f overlap.
		doc.chain.fps = 16;
		doc.chain.continuation = { overlap_frames: 16, stitch: true };
		doc.chain.segments = [
			chainSegment('s1', 'Lanterns', 49 / 16),
			chainSegment('s2', 'Steam', 97 / 16),
			chainSegment('s3', 'Across the street', 33 / 16)
		];
		const caps: DirectorCapabilities = {
			...h3Caps(),
			modes: { director: baseModeCap({ keyframes: 'first_only', maxFramesPerSegment: 81, continuation: { source: 'tail_frames', overlapFrames: 16, stitch: true }, maxOverlapFrames: 81 }) }
		};
		const model = deriveRailModel(doc, caps);
		const shot2 = model.shots[1];
		expect(shot2.totalFrames).toBe(97);
		expect(shot2.contributedFrames).toBe(81);
		expect(shot2.overCapBy).toBe(16);
		expect(shot2.capLocalFraction).toBeCloseTo(65 / 81, 5);
	});
});

describe('deriveRailModel — references lane (whole-form reference pool)', () => {
	it('the lane and capability fields are inactive when the mode declares no reference pool', () => {
		const model = deriveRailModel(h3Doc(), h3Caps());
		expect(model.lanes.references).toBe(false);
		expect(model.referencesCapability).toBeNull();
		expect(model.referenceFields).toEqual([]);
	});

	it('draws the lane and exposes the capability + fields under "whole" -- top-level, not per composition mode', () => {
		const caps: DirectorCapabilities = { ...h3Caps(), references: 'whole', referenceFields: ['references', 'reference_videos'] };
		const model = deriveRailModel(h3Doc(), caps);
		expect(model.lanes.references).toBe(true);
		expect(model.referencesCapability).toBe('whole');
		expect(model.referenceFields).toEqual(['references', 'reference_videos']);
	});

	it('draws the lane under "per_shot" too -- the strip is a read of the pool regardless of per-shot routing', () => {
		const caps: DirectorCapabilities = { ...h3Caps(), references: 'per_shot', referenceFields: ['references'] };
		const model = deriveRailModel(h3Doc(), caps);
		expect(model.lanes.references).toBe(true);
		expect(model.referencesCapability).toBe('per_shot');
	});
});

describe('deriveRailModel — chain continuation disabled (MiniMax-H3 refs mode)', () => {
	// h3Caps()'s own chain (h3Doc) has two structurally continuing joins
	// (prompt-only, non-first segments) -- continuationDisabled must force both
	// to cuts regardless, with zero overlap and no shoulder.
	function disabledCaps(): DirectorCapabilities {
		return { ...h3Caps(), modes: { director: baseModeCap({ ...h3Caps().modes.director, continuationDisabled: true }) } };
	}

	it('every seam is a cut, contributedFrames equals totalFrames (no overlap deducted)', () => {
		const model = deriveRailModel(h3Doc(), disabledCaps());
		expect(model.seams.every((s) => s.kind === 'cut')).toBe(true);
		expect(model.seams.every((s) => s.overlapFrames === 0)).toBe(true);
		expect(model.seams.every((s) => s.shoulderStartFrame === null)).toBe(true);
		expect(model.shots.every((s) => !s.hasOverlapIn && s.overlapInFrames === 0)).toBe(true);
		// Without any overlap deduction the total is the straight sum: 145+201+105.
		expect(model.totalFrames).toBe(451);
	});

	it('the same document under continuation still enabled keeps its continue joins (control)', () => {
		const model = deriveRailModel(h3Doc(), h3Caps());
		expect(model.seams.some((s) => s.kind === 'continue')).toBe(true);
		expect(model.totalFrames).toBe(417);
	});
});

// The full MERGED capability shape a real `refs`-mode request resolves to
// (mirrors presets/native/MiniMax-H3/preset.yml's `vars.video_director` +
// `preset_mode_overrides.refs` exactly): references per_shot, keyframes null
// (-> 'none'), audio false, continuation EXPLICITLY null (-> disabled),
// max_overlap_frames null -- all resolved through the real
// `resolveDirectorCapabilities` merge, not hand-assembled.
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

describe('deriveRailModel — H3 refs merged profile (references per_shot + keyframes none + audio off + continuation disabled)', () => {
	const refsCaps = resolveDirectorCapabilities(H3_REFS_PRESET_RAW, 'refs')!;

	it('resolves the exact merged shape the preset declares', () => {
		expect(refsCaps.references).toBe('per_shot');
		expect(refsCaps.referenceFields).toEqual(['references', 'reference_videos', 'reference_audios']);
		expect(refsCaps.modes.director?.keyframes).toBe('none');
		expect(refsCaps.modes.director?.audio).toBe(false);
		expect(refsCaps.modes.director?.continuationDisabled).toBe(true);
	});

	it('no keyframe or audio lane, but the references lane is on', () => {
		const model = deriveRailModel(h3Doc(), refsCaps);
		expect(model.lanes.keyframes).toBe(false);
		expect(model.lanes.audio).toBe(false);
		expect(model.lanes.references).toBe(true);
		expect(model.referencesCapability).toBe('per_shot');
	});

	it('every seam is cut-only', () => {
		const model = deriveRailModel(h3Doc(), refsCaps);
		expect(model.seams.every((s) => s.kind === 'cut')).toBe(true);
	});
});

describe('deriveRailModel — LTX timeline', () => {
	it('derives 297 frames of the 1001f cap from three non-overlapping blocks', () => {
		const model = deriveRailModel(ltxDoc(), ltxCaps());
		expect(model.routing).toBe('timeline');
		expect(model.shots.map((s) => s.contributedFrames)).toEqual([105, 110, 82]);
		expect(model.totalFrames).toBe(297);
		expect(model.totalOverCapBy).toBe(0);
		expect(model.maxFrames).toBe(1001);
	});

	it('draws the keyframes lane on the real LTX preset shape -- max_keyframes set, `keyframes` never declared (parses to \'none\')', () => {
		// presets/native/LTX-2/preset.yml and LTX-2.5/preset.yml both declare
		// max_keyframes with no `keyframes:` field at all. `keyframes: 'first_only'
		// | 'anywhere'` is chain-style vocabulary (it restricts a CHAIN
		// director's first-role/keyframe-role media); the timeline style has no
		// such gate -- docs/video-director.md: "Keyframes: Always, capped by
		// max_keyframes". ltxCaps() above declares 'anywhere' explicitly, which
		// masked this: a document built from a real LTX preset never does.
		const realLtxCaps: DirectorCapabilities = {
			...ltxCaps(),
			modes: { director: baseModeCap({ audio: true, icLora: true, maxKeyframes: 8 }) }
		};
		expect(realLtxCaps.modes.director!.keyframes).toBe('none');
		const model = deriveRailModel(ltxDoc(), realLtxCaps);
		expect(model.lanes).toEqual({ shots: true, keyframes: true, audio: true, icLora: true, references: false });
	});

	it('offers the free-keyframe lane on a bare, single-shot document -- timeline has no "+ Add shot" escalation to wait for', () => {
		// A user who hasn't placed a keyframe yet has a document that derives
		// to t2v (singleShotEdges: one segment, no edges, no keyframes/audio/
		// ic_lora -- see deriveDirectorMode). Chain routing gates free
		// placement on the document already being director-shaped because it
		// has an escalation path ("+ Add shot"); timeline routing has none, so
		// gating it the same way would permanently hide the lane a bare LTX
		// document needs to ever grow past t2v.
		const realLtxCaps: DirectorCapabilities = {
			...ltxCaps(),
			modes: { director: baseModeCap({ audio: true, icLora: true, maxKeyframes: 8 }) }
		};
		const bareDoc = ltxDoc();
		bareDoc.timeline.segments = [{ id: 'b1', start: 0, end: 4, text: '', prompt_segments: [] }];
		const model = deriveRailModel(bareDoc, realLtxCaps);
		expect(model.freePlacementActive).toBe(true);
		expect(model.lanes.keyframes).toBe(true);
	});

	it('draws keyframes, audio and the whole-video IC-LoRA head', () => {
		const doc = ltxDoc();
		doc.timeline.ic_lora = [{ id: 'ic-1', lora: { model: 'ltx-2-detailer', strength: 0.65 }, ref_media: { path: 'alley-plate.png' }, strength: 0.65 }];
		const model = deriveRailModel(doc, ltxCaps());
		expect(model.lanes).toEqual({ shots: true, keyframes: true, audio: true, icLora: true, references: false });
		expect(model.icLora).toEqual({ id: 'ic-1', hasLora: true, hasReference: true });
	});

	it('exposes a placeholder IC-LoRA head when the lane exists but nothing is set yet', () => {
		const model = deriveRailModel(ltxDoc(), ltxCaps());
		expect(model.icLora).toEqual({ id: 'ic-lora-head', hasLora: false, hasReference: false });
	});

	it('the rail content extent is the last block end, not the preset max duration', () => {
		const model = deriveRailModel(ltxDoc(), ltxCaps());
		expect(model.totalSeconds).toBeCloseTo(11.88, 2);
	});

	it('clamps a block-edge drag against its neighbour rather than crossing it', () => {
		const doc = ltxDoc();
		const clampedStart = resizeTimelineBlockEdge(doc.timeline.segments, 'b2', 'start', 2, doc.timeline.duration);
		expect(clampedStart).toBe(4.2); // block 1 ends at 4.2s; cannot cross it
		const clampedEnd = resizeTimelineBlockEdge(doc.timeline.segments, 'b2', 'end', 20, doc.timeline.duration);
		expect(clampedEnd).toBe(8.6); // block 3 starts at 8.6s; cannot cross it
		const withinRange = resizeTimelineBlockEdge(doc.timeline.segments, 'b2', 'start', 5, doc.timeline.duration);
		expect(withinRange).toBeCloseTo(5, 5);
	});
});

describe('drag reducers', () => {
	it('withChainKeyframeAt moves only the matching keyframe, leaving the rest of the doc untouched', () => {
		const doc = h3Doc();
		doc.chain.keyframes = [{ id: 'kf-1', at: 1, strength: 1, media: null }];
		const next = withChainKeyframeAt(doc, 'kf-1', 9.5);
		expect(next.chain.keyframes[0].at).toBe(9.5);
		expect(doc.chain.keyframes[0].at).toBe(1); // original untouched
		expect(next.chain.segments).toBe(doc.chain.segments); // unrelated branch not rebuilt
	});

	it('withTimelineKeyframeAt moves only the matching keyframe', () => {
		const doc = ltxDoc();
		doc.timeline.keyframes = [{ id: 'k1', start: 0, role: 'free', strength: 1, media: null }];
		const next = withTimelineKeyframeAt(doc, 'k1', 6);
		expect(next.timeline.keyframes[0].start).toBe(6);
	});

	it('withTimelineSegmentEdge moves only the matching block edge', () => {
		const doc = ltxDoc();
		const next = withTimelineSegmentEdge(doc, 'b2', 'start', 5);
		expect(next.timeline.segments.find((s) => s.id === 'b2')!.start).toBe(5);
		expect(next.timeline.segments.find((s) => s.id === 'b1')!.end).toBe(4.2); // untouched
	});
});

describe('deriveShotLabel', () => {
	it('uses the prompt\'s first clause, falling back to an ordinal when empty', () => {
		expect(deriveShotLabel('Lanterns, rain on paper lanterns', 0)).toBe('Lanterns');
		expect(deriveShotLabel('   ', 2)).toBe('Shot 3');
	});
});

// ─── Composition-scoped lanes + unified chain edge keyframes ──────────────
// H3 video mode: t2v/i2v/flf declared, chain director with keyframes
// 'anywhere' and audio -- the exact shape that prompted this: "why do I see
// audio in first-last frame mode".

function h3VideoCaps(overrides: Partial<DirectorModeCapability> = {}): DirectorCapabilities {
	return {
		presetModes: null,
		modes: {
			t2v: baseModeCap(),
			i2v: baseModeCap(),
			flf: baseModeCap(),
			director: baseModeCap({ keyframes: 'anywhere', maxSegments: 6, maxFramesPerSegment: 345, maxKeyframes: 8, audio: true, ...overrides })
		},
		enabledModes: ['t2v', 'i2v', 'flf', 'director'],
		defaultDuration: 5,
		defaultFps: 25,
		maxDuration: null,
		maxFrames: null,
		segmentRouting: true,
		references: null,
		referenceFields: []
	};
}

function h3SingleShotFlfDoc(): VideoDirectorValue {
	const doc = baseDoc();
	const filled = withChainTrailingMedia(
		withChainLeadingMedia(
			{ ...doc, chain: { ...doc.chain, fps: 25, segments: [chainSegment('h3-1', 'Stall row prompt', 145 / 25)] } },
			'h3-1',
			{ path: 'start.png' }
		),
		'h3-1',
		{ path: 'end.png' }
	);
	return filled;
}

describe('deriveRailModel — composition-scoped lanes (audio/ic-lora/free placement follow the DERIVED shape, not just capability)', () => {
	it('a single-shot flf-derived H3 document shows no audio lane and no free-placement affordance, despite the mode declaring audio + keyframes anywhere', () => {
		const model = deriveRailModel(h3SingleShotFlfDoc(), h3VideoCaps());
		expect(model.lanes.audio).toBe(false);
		expect(model.freePlacementActive).toBe(false);
	});

	it('the same document still shows the keyframes lane -- it renders the two locked edge mirrors', () => {
		const model = deriveRailModel(h3SingleShotFlfDoc(), h3VideoCaps());
		expect(model.lanes.keyframes).toBe(true);
		const ids = model.keyframes.map((k) => k.id);
		const firstId = chainEdgeKeyframeId('first', 'h3-1');
		const lastId = chainEdgeKeyframeId('last', 'h3-1');
		expect(ids).toEqual(expect.arrayContaining([firstId, lastId]));
		const first = model.keyframes.find((k) => k.id === firstId)!;
		const last = model.keyframes.find((k) => k.id === lastId)!;
		expect(first.role).toBe('first');
		expect(last.role).toBe('last');
		expect(isKeyframeLocked(first.role)).toBe(true);
		expect(isKeyframeLocked(last.role)).toBe(true);
		expect(first.atSeconds).toBe(0);
		expect(last.atSeconds).toBeCloseTo(model.totalSeconds, 5);
	});

	it('only the leading mirror renders when just the leading well is filled (i2v shape)', () => {
		const doc = baseDoc();
		const withLeading = withChainLeadingMedia(
			{ ...doc, chain: { ...doc.chain, fps: 25, segments: [chainSegment('h3-1', 'x', 145 / 25)] } },
			'h3-1',
			{ path: 'start.png' }
		);
		const model = deriveRailModel(withLeading, h3VideoCaps());
		const ids = model.keyframes.map((k) => k.id);
		expect(ids).toEqual([chainEdgeKeyframeId('first', 'h3-1')]);
	});

	it('adding a second shot (the existing escalation affordance) unlocks the full director lanes', () => {
		const single = h3SingleShotFlfDoc();
		const before = deriveRailModel(single, h3VideoCaps());
		expect(before.freePlacementActive).toBe(false);
		expect(before.lanes.audio).toBe(false);

		const escalated = withAddedShot(single, h3VideoCaps());
		expect(escalated.chain.segments).toHaveLength(2);
		const after = deriveRailModel(escalated, h3VideoCaps());
		expect(after.freePlacementActive).toBe(true);
		expect(after.lanes.audio).toBe(true);
	});

	it('never hides EXISTING content: a nominally single-shot chain doc with a foreign audio track still shows the audio lane', () => {
		const doc = baseDoc();
		doc.chain = {
			fps: 25,
			segments: [chainSegment('h3-1', 'x', 145 / 25)],
			continuation: { overlap_frames: 17, stitch: true },
			keyframes: [],
			audio: [{ id: 'a1', role: 'condition', start: 0, trim_start: 0, length: 5, media: { path: 'x.wav' } }]
		};
		const model = deriveRailModel(doc, h3VideoCaps());
		expect(model.lanes.audio).toBe(true);
	});

	it('a multi-shot chain never mirrors a trailing edge keyframe -- ChainSegment has no field for it beyond the single-shot case', () => {
		const doc = baseDoc();
		doc.chain = {
			fps: 25,
			segments: [chainSegment('h3-1', 'a', 145 / 25), chainSegment('h3-2', 'b', 145 / 25)],
			continuation: { overlap_frames: 17, stitch: true },
			keyframes: [],
			audio: []
		};
		doc.simple.last_frame = { path: 'stale.png' }; // orphaned by a since-added second shot
		const model = deriveRailModel(doc, h3VideoCaps());
		expect(model.keyframes.map((k) => k.id)).not.toContain('chain-edge-last');
	});
});

describe('isKeyframeLocked', () => {
	it('locks first/last edge roles, leaves free and chain "keyframe" roles draggable', () => {
		expect(isKeyframeLocked('first')).toBe(true);
		expect(isKeyframeLocked('last')).toBe(true);
		expect(isKeyframeLocked('free')).toBe(false);
		expect(isKeyframeLocked('keyframe')).toBe(false);
	});
});

describe('deriveRailModel — free keyframes lane visibility mirrors resolveDirectorEdgeAllowances', () => {
	it('timeline: i2v-only (no director declared) draws no lane, even though the leading edge is open', () => {
		const caps: DirectorCapabilities = {
			presetModes: null,
			modes: { t2v: baseModeCap(), i2v: baseModeCap() },
			enabledModes: ['t2v', 'i2v'],
			defaultDuration: 5,
			defaultFps: 25,
			maxDuration: null,
			maxFrames: null,
			segmentRouting: false,
			references: null,
			referenceFields: []
		};
		const doc = baseDoc();
		doc.timeline.segments = [{ id: 'tl-1', start: 0, end: 4, text: 'x', prompt_segments: [] }];
		const model = deriveRailModel(doc, caps);
		expect(model.lanes.keyframes).toBe(false);
	});

	it('chain: keyframes anywhere draws the lane even when flf is not declared', () => {
		const caps: DirectorCapabilities = {
			...h3Caps(),
			enabledModes: ['t2v', 'i2v', 'director']
		};
		const model = deriveRailModel(h3Doc(), caps);
		expect(model.lanes.keyframes).toBe(true);
	});

	it('a timeline keyframe placed via an edge well (role first/last) is locked; one added via the lane (role free) is not', () => {
		const doc = ltxDoc();
		doc.timeline.keyframes = [
			{ id: 'kf-first', start: 0, role: 'first', strength: 1, media: { path: 'a.png' } },
			{ id: 'kf-free', start: 6, role: 'free', strength: 1, media: null }
		];
		const model = deriveRailModel(doc, ltxCaps());
		const first = model.keyframes.find((k) => k.id === 'kf-first')!;
		const free = model.keyframes.find((k) => k.id === 'kf-free')!;
		expect(isKeyframeLocked(first.role)).toBe(true);
		expect(isKeyframeLocked(free.role)).toBe(false);
	});
});
