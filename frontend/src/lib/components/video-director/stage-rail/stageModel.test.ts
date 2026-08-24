import { describe, it, expect } from 'vitest';
import {
	deriveStageModel,
	overCapTrimSeconds,
	withDuplicatedShot,
	withSeamKind,
	withChainLeadingMedia,
	withChainTrailingMedia,
	withTimelineKeyframeMedia,
	withIcLoraPatch,
	withShotReferences,
	type StageShotModel,
	type StageJoinModel,
	type StageKeyframeModel
} from './stageModel';
import { chainEdgeKeyframeId, resolveDirectorCapabilities, resolveDirectorEdgeAllowances } from '$lib/utils/videoDirector';
import type { VideoDirectorValue, DirectorCapabilities, DirectorModeCapability, ChainSegment, DirectorPromptSegment, ChainKeyframe, DirectorKeyframe } from '$lib/types/videoDirector';

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
		chain: { fps: 16, segments: [], continuation: { overlap_frames: 0, stitch: true }, keyframes: [], audio: [] }
	};
}

function chainSegment(id: string, prompt: string, duration: number, overrides: Partial<ChainSegment> = {}): ChainSegment {
	return {
		id,
		prompt,
		prompt_segments: prompt ? [{ id: `${id}-p0`, content: prompt, chips: {}, type: 'content', enabled: true }] : [],
		duration,
		loras: null,
		keyframe: null,
		keyframe_strength: 1,
		last_keyframe: null,
		last_keyframe_strength: 1,
		sub_type_override: null,
		...overrides
	};
}

function tlSegment(id: string, text: string, start: number, end: number): DirectorPromptSegment {
	return { id, start, end, text, prompt_segments: text ? [{ id: `${id}-p0`, content: text, chips: {}, type: 'content', enabled: true }] : [] };
}

// ─── Wan-style chain: 3 shots, shot1 leading keyframe, continue then cut ────

function wanDoc(): VideoDirectorValue {
	const doc = baseDoc();
	doc.chain = {
		fps: 16,
		segments: [
			chainSegment('chain-1', 'Lanterns prompt', 49 / 16, { keyframe: { path: 'market-plate.png' } }),
			chainSegment('chain-2', 'Steam prompt', 81 / 16),
			chainSegment('chain-3', 'Across the street prompt', 33 / 16, { sub_type_override: 't2v' })
		],
		continuation: { overlap_frames: 16, stitch: true },
		keyframes: [],
		audio: []
	};
	return doc;
}

function wanCaps(): DirectorCapabilities {
	return {
		presetModes: null,
		modes: {
			director: baseModeCap({
				keyframes: 'first_only',
				maxSegments: 8,
				maxFramesPerSegment: 81,
				maxOverlapFrames: 81,
				perSegmentLoras: true
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

describe('deriveStageModel — chain gate precedence (Wan profile)', () => {
	const doc = wanDoc();
	const caps = wanCaps();

	it('shot 1 leading edge is a filled well', () => {
		const model = deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-1' });
		const shot = model.selected as StageShotModel;
		expect(shot.kind).toBe('shot');
		expect(shot.leadingGate).toEqual(
			expect.objectContaining({ kind: 'well', media: { path: 'market-plate.png' } })
		);
	});

	it('shot 1 trailing edge is never a well (no schema field) -- falls through to the outgoing seam statement', () => {
		const model = deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-1' });
		const shot = model.selected as StageShotModel;
		expect(shot.trailingGate.kind).toBe('statement');
		expect((shot.trailingGate as { label: string }).label).toBe('Continues into shot 2');
	});

	it('shot 2 has no well on either edge (first_only) -- leading is inherited, trailing states the cut', () => {
		const model = deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-2' });
		const shot = model.selected as StageShotModel;
		expect(shot.leadingGate).toEqual(expect.objectContaining({ kind: 'inherited', overlapFrames: 16 }));
		expect(shot.trailingGate).toEqual(expect.objectContaining({ kind: 'statement', label: 'Nothing carried forward' }));
	});

	it('shot 3 (hard cut) opens fresh, so its leading edge is an empty well -- a post-cut shot IS a first frame in its own generation; trailing edge still states end of video (this profile never declares flf)', () => {
		const model = deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-3' });
		const shot = model.selected as StageShotModel;
		expect(shot.leadingGate).toEqual(expect.objectContaining({ kind: 'well', media: null }));
		expect(shot.trailingGate).toEqual(expect.objectContaining({ kind: 'statement', label: 'End of video' }));
	});

	it('derives sub-type labels per shot', () => {
		const m1 = (deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-1' }).selected as StageShotModel).footer.shotTypeLabel;
		const m2 = (deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-2' }).selected as StageShotModel).footer.shotTypeLabel;
		const m3 = (deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-3' }).selected as StageShotModel).footer.shotTypeLabel;
		expect(m1).toBe('From start frame');
		expect(m2).toBe('Continued');
		expect(m3).toBe('Text only');
	});

	it('reports the shot 2 frame math: 81 own frames, 65 contributed as new', () => {
		const shot = deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-2' }).selected as StageShotModel;
		expect(shot.footer.frames).toBe(81);
		expect(shot.footer.newFrames).toBe(65);
	});
});

describe('deriveStageModel — join (seam) math', () => {
	const doc = wanDoc();
	const caps = wanCaps();

	it('continue join 1→2: sentence, overlap and adds-frames arithmetic', () => {
		const model = deriveStageModel(doc, caps, { kind: 'seam', id: 'seam-chain-1-chain-2' });
		const join = model.selected as StageJoinModel;
		expect(join.kind).toBe('seam');
		expect(join.isCut).toBe(false);
		expect(join.overlapFrames).toBe(16);
		expect(join.addsFrames).toBe(65);
		expect(join.tailFrameLabel).toContain('frame 49');
		expect(join.headFrameLabel).toContain('frame 1');
		expect(join.sentence).toContain('re-generates the last 16 frames');
		expect(join.sentence).toContain('adds 65 new frames');
	});

	it('cut join 2→3: total recomputed live matches the worked 49+81+33 arithmetic', () => {
		const model = deriveStageModel(doc, caps, { kind: 'seam', id: 'seam-chain-2-chain-3' });
		const join = model.selected as StageJoinModel;
		expect(join.isCut).toBe(true);
		expect(join.sentence).toContain('lands exactly on frame 115');
		expect(join.chainTotals).toEqual({
			fps: 16,
			generatedFrames: [49, 81, 33],
			deductions: [
				{ seamLabel: '1→2', frames: 16, isCut: false },
				{ seamLabel: '2→3', frames: 0, isCut: true }
			],
			totalFrames: 147,
			totalSeconds: 9.1875
		});
	});
});

describe('overCapTrimSeconds', () => {
	it('is null within cap', () => {
		expect(overCapTrimSeconds({ overCapBy: 0, capFrames: 81 }, 16)).toBeNull();
	});

	it('lands the trim exactly on the cap for a shot over budget', () => {
		const doc = wanDoc();
		doc.chain.segments[1] = chainSegment('chain-2', 'Steam prompt', 97 / 16);
		const caps = wanCaps();
		const shot = deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-2' }).selected as StageShotModel;
		expect(shot.overCap).toBe(true);
		expect(shot.footer.overCapBy).toBe(16);
		expect(shot.trimToSeconds).toBeCloseTo(81 / 16, 6);
	});
});

// ─── MiniMax-H3-style chain: keyframes anywhere, both continue ─────────────

function h3Doc(): VideoDirectorValue {
	const doc = baseDoc();
	doc.chain = {
		fps: 25,
		segments: [
			chainSegment('h3-1', 'Stall row prompt', 145 / 25),
			chainSegment('h3-2', 'The wok prompt', 201 / 25),
			chainSegment('h3-3', 'Looking up prompt', 105 / 25)
		],
		continuation: { overlap_frames: 17, stitch: true },
		keyframes: [{ id: 'kf-3', at: 11.24, strength: 1, media: { path: 'her-face.png' } } satisfies ChainKeyframe],
		audio: []
	};
	return doc;
}

function h3Caps(): DirectorCapabilities {
	return {
		presetModes: null,
		modes: {
			director: baseModeCap({ keyframes: 'anywhere', maxSegments: 6, maxFramesPerSegment: 345, maxKeyframes: 8, audio: true })
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

describe('deriveStageModel — H3 profile (keyframes anywhere)', () => {
	it('shot 1 still gets a leading well under "anywhere", not just "first_only"', () => {
		const model = deriveStageModel(h3Doc(), h3Caps(), { kind: 'shot', id: 'h3-1' });
		const shot = model.selected as StageShotModel;
		expect(shot.leadingGate.kind).toBe('well');
	});

	it('landing window: 11.24s lands in shot 2 at local frame 153/201', () => {
		const model = deriveStageModel(h3Doc(), h3Caps(), { kind: 'keyframe', id: 'kf-3' });
		const kf = model.selected as StageKeyframeModel;
		expect(kf.kind).toBe('keyframe');
		expect(kf.atFrame).toBe(281);
		expect(kf.totalFrames).toBe(417);
		expect(kf.landing).toEqual({ shotIndex: 1, shotLabel: 'The wok prompt', localFrame: 153, localTotalFrames: 201 });
		expect(kf.snapped).toBe(false);
	});
});

// ─── H3 video mode: t2v/i2v/flf + chain anywhere -- the single-shot flf ────
// trailing well this task adds (contract item 3). Distinct from h3Doc()/
// h3Caps() above, which only declare `director` (no t2v/i2v/flf) and never
// exercised this path.

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

describe('deriveStageModel — H3 video mode (t2v/i2v/flf + chain anywhere): single-shot trailing well', () => {
	it('a single chain shot offers a well on BOTH edges', () => {
		const doc = baseDoc();
		doc.chain = {
			fps: 25,
			segments: [chainSegment('h3-1', 'Stall row prompt', 145 / 25)],
			continuation: { overlap_frames: 17, stitch: true },
			keyframes: [],
			audio: []
		};
		const shot = deriveStageModel(doc, h3VideoCaps(), { kind: 'shot', id: 'h3-1' }).selected as StageShotModel;
		expect(shot.leadingGate.kind).toBe('well');
		expect(shot.trailingGate.kind).toBe('well');
	});

	it('a 3-shot chain with no cuts keeps no trailing well anywhere -- every segment still continues', () => {
		const doc = h3Doc(); // 3 segments, no cuts
		for (const id of ['h3-1', 'h3-2', 'h3-3']) {
			const shot = deriveStageModel(doc, h3VideoCaps(), { kind: 'shot', id }).selected as StageShotModel;
			expect(shot.trailingGate.kind).not.toBe('well');
		}
	});

	it('cutting the seam before a shot ALSO opens a trailing well on the shot before it', () => {
		const doc = h3Doc(); // 3 segments, h3-2 continues from h3-1 by default
		doc.chain.segments[1] = { ...doc.chain.segments[1], sub_type_override: 't2v' };
		// h3-1's OUTGOING join is now a cut (h3-2 opens fresh via its own
		// override) -- h3-1 already opens fresh (index 0), so it now closes
		// fresh too and gets a trailing well.
		const first = deriveStageModel(doc, h3VideoCaps(), { kind: 'shot', id: 'h3-1' }).selected as StageShotModel;
		expect(first.leadingGate.kind).toBe('well');
		expect(first.trailingGate.kind).toBe('well');
		// h3-2 opens fresh (its own cut) but still continues INTO h3-3 (no
		// override there), so it gets a leading well but no trailing one.
		const second = deriveStageModel(doc, h3VideoCaps(), { kind: 'shot', id: 'h3-2' }).selected as StageShotModel;
		expect(second.leadingGate.kind).toBe('well');
		expect(second.trailingGate.kind).not.toBe('well');
		// h3-3 still continues FROM h3-2 (no override of its own), so it never
		// opens fresh -- no wells on either edge.
		const third = deriveStageModel(doc, h3VideoCaps(), { kind: 'shot', id: 'h3-3' }).selected as StageShotModel;
		expect(third.leadingGate.kind).not.toBe('well');
		expect(third.trailingGate.kind).not.toBe('well');
	});

	it('withChainTrailingMedia writes/clears segment 0\'s own last_keyframe, which the trailing well then reflects', () => {
		const doc = baseDoc();
		doc.chain = {
			fps: 25,
			segments: [chainSegment('h3-1', 'Stall row prompt', 145 / 25)],
			continuation: { overlap_frames: 17, stitch: true },
			keyframes: [],
			audio: []
		};
		const filled = withChainTrailingMedia(doc, 'h3-1', { path: 'end-frame.png' });
		expect(filled.chain.segments[0].last_keyframe).toEqual({ path: 'end-frame.png' });
		const shot = deriveStageModel(filled, h3VideoCaps(), { kind: 'shot', id: 'h3-1' }).selected as StageShotModel;
		expect(shot.trailingGate).toEqual(expect.objectContaining({ kind: 'well', media: { path: 'end-frame.png' } }));

		const cleared = withChainTrailingMedia(filled, 'h3-1', null);
		expect(cleared.chain.segments[0].last_keyframe).toBeNull();
	});

	it('a chain mode with `keyframes` capability but no flf mode declared still opens the single-shot trailing well via free placement', () => {
		const caps: DirectorCapabilities = { ...h3VideoCaps(), enabledModes: ['t2v', 'i2v', 'director'], modes: { t2v: baseModeCap(), i2v: baseModeCap(), director: h3VideoCaps().modes.director } };
		expect(caps.enabledModes).not.toContain('flf');
		const doc = baseDoc();
		doc.chain = { fps: 25, segments: [chainSegment('h3-1', 'x', 145 / 25)], continuation: { overlap_frames: 17, stitch: true }, keyframes: [], audio: [] };
		const shot = deriveStageModel(doc, caps, { kind: 'shot', id: 'h3-1' }).selected as StageShotModel;
		expect(shot.trailingGate.kind).toBe('well');
	});
});

// ─── One keyframe system: chain-edge mirrors are selectable/editable from ──
// the lane, and it's the SAME write the shot-edit well makes -- no duplicate
// storage.

describe('deriveStageModel — chain-edge keyframe mirrors (unified with the shot-edit wells)', () => {
	function singleShotDoc(): VideoDirectorValue {
		const doc = baseDoc();
		doc.chain = {
			fps: 25,
			segments: [
				chainSegment('h3-1', 'x', 145 / 25, {
					keyframe: { path: 'start.png' },
					keyframe_strength: 0.8,
					last_keyframe: { path: 'end.png' }
				})
			],
			continuation: { overlap_frames: 17, stitch: true },
			keyframes: [],
			audio: []
		};
		return doc;
	}

	it('selecting the leading chain-edge mirror reads the same value as the leading well, locked to the start', () => {
		const model = deriveStageModel(singleShotDoc(), h3VideoCaps(), { kind: 'keyframe', id: chainEdgeKeyframeId('first', 'h3-1') });
		const kf = model.selected as StageKeyframeModel;
		expect(kf.kind).toBe('keyframe');
		expect(kf.media).toEqual({ path: 'start.png' });
		expect(kf.strength).toBe(0.8);
		expect(kf.role).toBe('first');
		expect(kf.atSeconds).toBe(0);
	});

	it('selecting the trailing chain-edge mirror reads segment 0\'s own last_keyframe, locked to the end', () => {
		const doc = singleShotDoc();
		const model = deriveStageModel(doc, h3VideoCaps(), { kind: 'keyframe', id: chainEdgeKeyframeId('last', 'h3-1') });
		const kf = model.selected as StageKeyframeModel;
		expect(kf.media).toEqual({ path: 'end.png' });
		expect(kf.role).toBe('last');
		expect(kf.atSeconds).toBeCloseTo(kf.totalFrames / 25, 5);
	});

	it('clearing the lane-side leading selection clears the SAME value the shot-edit well reads -- no duplicate storage', () => {
		const doc = singleShotDoc();
		const cleared = withChainLeadingMedia(doc, 'h3-1', null);
		expect(cleared.chain.segments[0].keyframe).toBeNull();
		// The well (StageShot's leadingGate) reflects the same clear.
		const shot = deriveStageModel(cleared, h3VideoCaps(), { kind: 'shot', id: 'h3-1' }).selected as StageShotModel;
		expect(shot.leadingGate).toEqual(expect.objectContaining({ kind: 'well', media: null }));
		// And the lane no longer mirrors it.
		expect(deriveStageModel(cleared, h3VideoCaps(), { kind: 'keyframe', id: chainEdgeKeyframeId('first', 'h3-1') }).selected.kind).toBe('empty');
	});

	it('filling via withChainTrailingMedia (the well setter) is exactly what the lane-side model reads back', () => {
		const doc = baseDoc();
		doc.chain = { fps: 25, segments: [chainSegment('h3-1', 'x', 145 / 25)], continuation: { overlap_frames: 17, stitch: true }, keyframes: [], audio: [] };
		const filled = withChainTrailingMedia(doc, 'h3-1', { path: 'end2.png' });
		const kf = deriveStageModel(filled, h3VideoCaps(), { kind: 'keyframe', id: chainEdgeKeyframeId('last', 'h3-1') }).selected as StageKeyframeModel;
		expect(kf.media).toEqual({ path: 'end2.png' });
	});

	it('a dangling chain-edge selection (the well was never filled) falls back to empty', () => {
		const doc = baseDoc();
		doc.chain = { fps: 25, segments: [chainSegment('h3-1', 'x', 145 / 25)], continuation: { overlap_frames: 17, stitch: true }, keyframes: [], audio: [] };
		expect(deriveStageModel(doc, h3VideoCaps(), { kind: 'keyframe', id: chainEdgeKeyframeId('first', 'h3-1') }).selected.kind).toBe('empty');
		expect(deriveStageModel(doc, h3VideoCaps(), { kind: 'keyframe', id: chainEdgeKeyframeId('last', 'h3-1') }).selected.kind).toBe('empty');
	});
});

// ─── Timeline t2v/i2v-only: locked edges, no keyframes lane ────────────────

describe('deriveStageModel — timeline edge allowances (t2v-only / i2v-only, no free placement)', () => {
	function singleBlockDoc(): VideoDirectorValue {
		const doc = baseDoc();
		doc.timeline = { duration: 4, fps: 25, segments: [tlSegment('tl-1', 'x', 0, 4)], keyframes: [], audio: [], ic_lora: [] };
		return doc;
	}

	it('t2v-only: neither edge is a well', () => {
		const caps: DirectorCapabilities = {
			presetModes: null,
			modes: { t2v: baseModeCap() },
			enabledModes: ['t2v'],
			defaultDuration: 5,
			defaultFps: 25,
			maxDuration: null,
			maxFrames: null,
			segmentRouting: false,
			references: null,
			referenceFields: []
		};
		expect(resolveDirectorEdgeAllowances(caps)).toEqual({ freePlacementAllowed: false, leadingEdgeAllowed: false, trailingEdgeAllowed: false });
		const shot = deriveStageModel(singleBlockDoc(), caps, { kind: 'shot', id: 'tl-1' }).selected as StageShotModel;
		expect(shot.leadingGate.kind).not.toBe('well');
		expect(shot.trailingGate.kind).not.toBe('well');
	});

	it('i2v-only (no director): leading well only, trailing falls through to a statement', () => {
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
		const shot = deriveStageModel(singleBlockDoc(), caps, { kind: 'shot', id: 'tl-1' }).selected as StageShotModel;
		expect(shot.leadingGate.kind).toBe('well');
		expect(shot.trailingGate.kind).toBe('statement');
	});
});

// ─── LTX-style timeline: 3 timed blocks, keyframes anywhere ────────────────

function ltxCaps(): DirectorCapabilities {
	return {
		presetModes: null,
		modes: { director: baseModeCap({ keyframes: 'anywhere', audio: true, icLora: true, maxKeyframes: 8 }) },
		enabledModes: ['director'],
		defaultDuration: 5,
		defaultFps: 25,
		maxDuration: null,
		maxFrames: 1001,
		segmentRouting: false,
		references: null,
		referenceFields: []
	};
}

describe('deriveStageModel — LTX profile (timeline)', () => {
	it('an existing keyframe landing exactly on a block boundary takes the gate', () => {
		const doc = baseDoc();
		doc.timeline = {
			duration: 11.88,
			fps: 25,
			segments: [
				tlSegment('tl-1', 'Rain on the neon sign', 0, 4.2),
				tlSegment('tl-2', 'Past the noodle window', 4.2, 8.6),
				tlSegment('tl-3', 'The sign goes out', 8.6, 11.88)
			],
			keyframes: [{ id: 'tl-kf-2', start: 4.2, role: 'free', strength: 1, media: { path: 'window.png' } } satisfies DirectorKeyframe],
			audio: [],
			ic_lora: []
		};
		const model = deriveStageModel(doc, ltxCaps(), { kind: 'shot', id: 'tl-2' });
		const shot = model.selected as StageShotModel;
		expect(shot.leadingGate).toEqual(expect.objectContaining({ kind: 'keyframe', keyframeId: 'tl-kf-2', media: { path: 'window.png' } }));
		expect(shot.trailingGate).toEqual(expect.objectContaining({ kind: 'well' }));
	});

	it('a single block spanning the whole video offers a well on BOTH edges (the flf shape)', () => {
		const doc = baseDoc();
		doc.timeline = {
			duration: 4.06,
			fps: 25,
			segments: [tlSegment('tl-only', 'Handheld push down the stall row', 0, 4.06)],
			keyframes: [],
			audio: [],
			ic_lora: []
		};
		const model = deriveStageModel(doc, ltxCaps(), { kind: 'shot', id: 'tl-only' });
		const shot = model.selected as StageShotModel;
		expect(shot.leadingGate.kind).toBe('well');
		expect(shot.trailingGate.kind).toBe('well');
	});

	it('edges stay wells regardless of the (chain-only) keyframes capability value -- the real LTX preset shape', () => {
		// docs/video-director.md: timeline-style keyframes are "Always" legal,
		// capped only by max_keyframes -- `keyframes: 'first_only'|'anywhere'`
		// is chain-style vocabulary the timeline style never reads. Every real
		// LTX preset (presets/native/LTX-2, LTX-2.5) declares `max_keyframes`
		// with no `keyframes` field at all, which parses to 'none' here.
		const caps = ltxCaps();
		caps.modes.director = baseModeCap({ keyframes: 'none', maxKeyframes: 8 });
		const doc = baseDoc();
		doc.timeline = { duration: 4.06, fps: 25, segments: [tlSegment('tl-only', 'x', 0, 4.06)], keyframes: [], audio: [], ic_lora: [] };
		const shot = deriveStageModel(doc, caps, { kind: 'shot', id: 'tl-only' }).selected as StageShotModel;
		expect(shot.leadingGate.kind).toBe('well');
		expect(shot.trailingGate.kind).toBe('well');
	});

});

// ─── Empty state ────────────────────────────────────────────────────────────

describe('deriveStageModel — empty document', () => {
	it('a single, prompt-less chain shot shows the teaching copy', () => {
		const doc = baseDoc();
		doc.chain = {
			fps: 16,
			segments: [chainSegment('chain-0', '', 65 / 16)],
			continuation: { overlap_frames: 4, stitch: true },
			keyframes: [],
			audio: []
		};
		const caps = wanCaps();
		const shot = deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-0' }).selected as StageShotModel;
		expect(shot.isPromptEmpty).toBe(true);
		expect(shot.showTeachingCopy).toBe(true);
		expect(shot.leadingGate.kind).toBe('well');
	});

	it('a dangling selection (removed object) falls back to the empty kind', () => {
		const model = deriveStageModel(wanDoc(), wanCaps(), { kind: 'shot', id: 'no-such-shot' });
		expect(model.selected.kind).toBe('empty');
	});

	it('no selection at all is the empty kind', () => {
		const model = deriveStageModel(wanDoc(), wanCaps(), null);
		expect(model.selected.kind).toBe('empty');
	});
});

// ─── Pure reducers ──────────────────────────────────────────────────────────

describe('withDuplicatedShot', () => {
	it('inserts a copy right after the source, never carrying its leading keyframe', () => {
		const doc = wanDoc();
		const next = withDuplicatedShot(doc, wanCaps(), 'chain-1');
		expect(next.chain.segments).toHaveLength(4);
		expect(next.chain.segments[1].prompt).toBe('Lanterns prompt');
		expect(next.chain.segments[1].id).not.toBe('chain-1');
		expect(next.chain.segments[1].keyframe).toBeNull();
		// original untouched (immutability)
		expect(doc.chain.segments).toHaveLength(3);
	});
});

describe('withSeamKind', () => {
	it('flips a continue join to a hard cut by setting the next shot sub_type_override', () => {
		const doc = wanDoc();
		const next = withSeamKind(doc, wanCaps(), 'seam-chain-1-chain-2', 'cut');
		expect(next.chain.segments[1].sub_type_override).toBe('t2v');
	});

	it('flips a cut back to continue by clearing the override', () => {
		const doc = wanDoc();
		const next = withSeamKind(doc, wanCaps(), 'seam-chain-2-chain-3', 'continue');
		expect(next.chain.segments[2].sub_type_override).toBeNull();
	});
});

describe('withChainLeadingMedia / withTimelineKeyframeMedia / withIcLoraPatch', () => {
	it('sets and clears the chain leading well', () => {
		const doc = wanDoc();
		const attached = withChainLeadingMedia(doc, 'chain-1', { path: 'new.png' }, 0.8);
		expect(attached.chain.segments[0].keyframe).toEqual({ path: 'new.png' });
		expect(attached.chain.segments[0].keyframe_strength).toBe(0.8);
		const cleared = withChainLeadingMedia(attached, 'chain-1', null);
		expect(cleared.chain.segments[0].keyframe).toBeNull();
	});

	it('mints a new timeline keyframe and later clears it', () => {
		const doc = baseDoc();
		const withKf = withTimelineKeyframeMedia(doc, 'new-kf', 'first', 0, { path: 'a.png' });
		expect(withKf.timeline.keyframes).toHaveLength(1);
		expect(withKf.timeline.keyframes[0]).toEqual({ id: 'new-kf', start: 0, role: 'first', strength: 1, media: { path: 'a.png' } });
		const cleared = withTimelineKeyframeMedia(withKf, 'new-kf', 'first', 0, null);
		expect(cleared.timeline.keyframes).toHaveLength(0);
	});

	it('upserts an IC-LoRA entry by id', () => {
		const doc = baseDoc();
		const next = withIcLoraPatch(doc, 'ic-1', { lora: { model: 'ltx-2-detailer', strength: 0.65 } });
		expect(next.timeline.ic_lora).toEqual([{ id: 'ic-1', lora: { model: 'ltx-2-detailer', strength: 0.65 }, ref_media: null, strength: 1 }]);
	});
});

describe('withShotReferences', () => {
	it('writes a per-shot selection onto a chain segment', () => {
		const doc = wanDoc();
		const next = withShotReferences(doc, wanCaps(), 'chain-2', [{ path: '/pool/a.png' }]);
		expect(next.chain.segments[1].references).toEqual([{ path: '/pool/a.png' }]);
		expect(next.chain.segments[0].references).toBeUndefined();
	});

	it('an empty selection falls back to "All" (undefined), never a stored []', () => {
		const doc = wanDoc();
		doc.chain.segments[1].references = [{ path: '/pool/a.png' }];
		const next = withShotReferences(doc, wanCaps(), 'chain-2', []);
		expect(next.chain.segments[1].references).toBeUndefined();
	});

	it('writes onto a timeline segment under non-segment-routing caps', () => {
		const doc = baseDoc();
		doc.timeline.segments = [tlSegment('s1', 'a', 0, 5)];
		const caps: DirectorCapabilities = { ...wanCaps(), segmentRouting: false };
		const next = withShotReferences(doc, caps, 's1', [{ path: '/pool/b.png' }]);
		expect(next.timeline.segments[0].references).toEqual([{ path: '/pool/b.png' }]);
	});
});

describe('StageShotFooter.references — existence per capability', () => {
	const formData = {
		references: [{ path: '/pool/a.png' }, { path: '/pool/b.png' }],
		reference_videos: [{ path: '/pool/c.mp4' }]
	};

	it('is null when the mode declares no references capability', () => {
		const doc = wanDoc();
		const caps = wanCaps();
		const shot = deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-1' }, formData).selected as StageShotModel;
		expect(shot.footer.references).toBeNull();
	});

	it('reads "Whole film" (poolCount, no per-shot count) under the whole capability', () => {
		const doc = wanDoc();
		const caps: DirectorCapabilities = { ...wanCaps(), references: 'whole', referenceFields: ['references', 'reference_videos'] };
		const shot = deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-1' }, formData).selected as StageShotModel;
		expect(shot.footer.references).toEqual({ capability: 'whole', poolCount: 3, selectedCount: null });
	});

	it('per_shot with no explicit selection reads as "All (poolCount)"', () => {
		const doc = wanDoc();
		const caps: DirectorCapabilities = { ...wanCaps(), references: 'per_shot', referenceFields: ['references', 'reference_videos'] };
		const shot = deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-1' }, formData).selected as StageShotModel;
		expect(shot.footer.references).toEqual({ capability: 'per_shot', poolCount: 3, selectedCount: null });
	});

	it('per_shot with an explicit selection reads its count against the pool count', () => {
		const doc = wanDoc();
		doc.chain.segments[0].references = [{ path: '/pool/a.png' }];
		const caps: DirectorCapabilities = { ...wanCaps(), references: 'per_shot', referenceFields: ['references', 'reference_videos'] };
		const shot = deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-1' }, formData).selected as StageShotModel;
		expect(shot.footer.references).toEqual({ capability: 'per_shot', poolCount: 3, selectedCount: 1 });
	});

	it('poolCount only counts items on the mode\'s declared reference_fields', () => {
		const doc = wanDoc();
		const caps: DirectorCapabilities = { ...wanCaps(), references: 'whole', referenceFields: ['references'] }; // excludes reference_videos
		const shot = deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-1' }, formData).selected as StageShotModel;
		expect(shot.footer.references?.poolCount).toBe(2);
	});
});

// The full MERGED capability shape a real `refs`-mode request resolves to
// (mirrors presets/native/MiniMax-H3/preset.yml exactly) -- see the matching
// fixture in railModel.test.ts for the raw shape this is derived from.
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

describe('deriveStageModel — H3 refs merged profile', () => {
	const refsCaps = resolveDirectorCapabilities(H3_REFS_PRESET_RAW, 'refs')!;
	const formData = { references: [{ path: '/pool/a.png' }] };

	it('no edge wells anywhere -- keyframes:none means every gate falls through to inherited/statement', () => {
		const doc = wanDoc();
		for (const id of ['chain-1', 'chain-2', 'chain-3']) {
			const shot = deriveStageModel(doc, refsCaps, { kind: 'shot', id }, formData).selected as StageShotModel;
			expect(shot.leadingGate.kind).not.toBe('well');
			expect(shot.trailingGate.kind).not.toBe('well');
		}
	});

	it('the References footer cell is present on every shot', () => {
		const doc = wanDoc();
		const shot = deriveStageModel(doc, refsCaps, { kind: 'shot', id: 'chain-1' }, formData).selected as StageShotModel;
		expect(shot.footer.references).not.toBeNull();
		expect(shot.footer.references?.capability).toBe('per_shot');
	});

	it('the join is a permanent cut with the toggle unavailable', () => {
		const doc = wanDoc();
		const join = deriveStageModel(doc, refsCaps, { kind: 'seam', id: 'seam-chain-1-chain-2' }, formData).selected as StageJoinModel;
		expect(join.isCut).toBe(true);
		expect(join.continuationAvailable).toBe(false);
	});
});
