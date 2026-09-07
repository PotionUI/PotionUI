import { describe, it, expect } from 'vitest';
import { deriveShotRail, buildRailTicks, railTimeFromFraction } from './shotRailModel';
import { chainEdgeKeyframeId, parseChainEdgeKeyframeId } from '$lib/utils/videoDirector';
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
		fpsLocked: false,
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
		timeline: {
			fps: 24,
			shots: [{ id: 'shot-1', duration: 5, continue_from_previous: false, segments: [], keyframes: [], audio: [], ic_lora: [] }]
		},
		chain: { fps: 16, segments: [], continuation: { overlap_frames: 0, stitch: true }, keyframes: [], audio: [] }
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
		steps: null,
		cfg: null,
		sub_type_override: override
	};
}

function wanCaps(): DirectorCapabilities {
	return {
		presetModes: null,
		modes: {
			director: baseModeCap({
				perSegmentLoras: true,
				keyframes: 'first_only',
				maxSegments: 8,
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
		chainSegment('s1', 'Lanterns', 49 / 16), // 3.0625s
		chainSegment('s2', 'Steam', 81 / 16), // 5.0625s
		chainSegment('s3', 'Across the street', 33 / 16, 't2v')
	];
	return doc;
}

function h3Caps(): DirectorCapabilities {
	return {
		presetModes: null,
		modes: {
			director: baseModeCap({
				audio: true,
				maxKeyframes: 8,
				keyframes: 'anywhere',
				maxSegments: 6,
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
		chainSegment('h1', 'Stall row', 145 / 25), // 5.8s
		chainSegment('h2', 'The wok', 201 / 25), // 8.04s
		chainSegment('h3', 'Looking up', 105 / 25)
	];
	return doc;
}

function ltxCaps(): DirectorCapabilities {
	return {
		presetModes: null,
		modes: { director: baseModeCap({ audio: true, icLora: true, maxKeyframes: null, keyframes: 'anywhere' }) },
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
	doc.timeline = {
		...doc.timeline,
		fps: 25,
		shots: [
			{
				// Below the content end (8.6) on purpose -- the rail total is
				// max(shot.duration, contentEnd); see railModel.test.ts's own
				// dedicated test for that relationship.
				...doc.timeline.shots[0],
				duration: 1,
				segments: [
					{ id: 'b1', start: 0, end: 4.2, text: 'Rain on the neon sign', prompt_segments: [] },
					{ id: 'b2', start: 5.5, end: 8.6, text: 'Past the noodle window', prompt_segments: [] } // gap 4.2-5.5 -> global fill
				]
			}
		]
	};
	return doc;
}

describe('buildRailTicks — fixed major/minor tick rule', () => {
	it('a whole-second duration: one major tick per second, no duplicate final tick, minors at .25 offsets', () => {
		const ticks = buildRailTicks(3);
		const majors = ticks.filter((t) => t.major);
		expect(majors.map((t) => t.label)).toEqual(['0s', '1s', '2s', '3s']);
		expect(majors[majors.length - 1].atPercent).toBe(100);
		const minors = ticks.filter((t) => !t.major);
		expect(minors).toHaveLength(3 * 3); // .25/.5/.75 within each of the 3 whole seconds
		expect(minors.every((t) => t.label === null)).toBe(true);
	});

	it('a non-integer duration always gets a final RIGHT-ALIGNED major tick at the exact duration', () => {
		const ticks = buildRailTicks(5.1);
		const majors = ticks.filter((t) => t.major);
		expect(majors.map((t) => t.label)).toEqual(['0s', '1s', '2s', '3s', '4s', '5s', '5.1s']);
		expect(majors[majors.length - 1].atPercent).toBe(100);
	});

	it('never double-counts a final tick that already lands on a whole second', () => {
		const ticks = buildRailTicks(4);
		const majors = ticks.filter((t) => t.major);
		expect(majors.filter((t) => t.atPercent === 100)).toHaveLength(1);
	});

	it('a sub-1s duration still gets a right-aligned final tick and no out-of-range minors', () => {
		const ticks = buildRailTicks(0.6);
		expect(ticks.filter((t) => t.major).map((t) => t.label)).toEqual(['0s', '0.6s']);
		expect(ticks.every((t) => t.atPercent <= 100)).toBe(true);
	});
});

describe('railTimeFromFraction — snapped to 0.25s and clamped', () => {
	const rail = deriveShotRail(wanDoc(), wanCaps(), 's1');

	it('snaps to the nearest quarter-second', () => {
		expect(railTimeFromFraction(rail, 0.3)).toBeCloseTo(Math.round((0.3 * rail.durationSeconds) / 0.25) * 0.25, 5);
	});

	it('clamps outside [0,1] fractions into range (still snapped to 0.25s)', () => {
		expect(railTimeFromFraction(rail, -1)).toBe(0);
		const atMax = railTimeFromFraction(rail, 2);
		expect(atMax).toBeLessThanOrEqual(rail.durationSeconds);
		expect(atMax).toBeGreaterThan(rail.durationSeconds - 0.25);
	});
});

describe('deriveShotRail — chain routing (Wan, first_only: no free keyframes/audio lane)', () => {
	it("uses the segment's own generated duration, not its post-overlap contributed length", () => {
		const rail = deriveShotRail(wanDoc(), wanCaps(), 's2');
		expect(rail.durationSeconds).toBeCloseTo(81 / 16, 5); // NOT (81-16)/16
	});

	it('prompt lane is always one full-span beat with adding disabled (D3 ruling)', () => {
		const rail = deriveShotRail(wanDoc(), wanCaps(), 's1');
		expect(rail.lanes.prompt).not.toBeNull();
		expect(rail.lanes.prompt!.beats).toHaveLength(1);
		expect(rail.lanes.prompt!.beats[0]).toMatchObject({ startPercent: 0, widthPercent: 100, global: false });
		expect(rail.lanes.prompt!.canAdd).toBe(false);
		expect(rail.lanes.prompt!.addDisabledReason).toBeTruthy();
	});

	it('the keyframes lane opens on shot 1 under first_only, even with no media placed yet; audio stays gated on capability', () => {
		const rail = deriveShotRail(wanDoc(), wanCaps(), 's1');
		expect(rail.lanes.keyframes).not.toBeNull();
		expect(rail.lanes.audio).toBeNull();
	});

	it('shot 1 gets a START anchor only (it opens fresh); no END -- first_only never allows a trailing well, and shot 1 continues into shot 2; canAdd stays locked', () => {
		// railModel.ts's `chainEdgeAllowed` opens the LANE off
		// `resolveDirectorEdgeAllowances(caps).leadingEdgeAllowed` alone (true
		// for first_only) -- but WHICH anchor each shot draws is the join-aware
		// `chainSegmentEdgeAllowances` question: shot 1 opens fresh (it's
		// index 0) so it gets a START well, but it continues INTO shot 2
		// (wanDoc's first join is a `continue`), so `closesFresh[0]` is false
		// and it gets no END well -- and Wan's own caps never declare `flf` or
		// `keyframes: 'anywhere'`, so no shot of this family could ever draw
		// one regardless of join topology. `canAdd` (the free "+" add) stays
		// false regardless: it reads `rail.freePlacementActive`, which
		// first_only (keyframes !== 'anywhere') never sets.
		const rail = deriveShotRail(wanDoc(), wanCaps(), 's1');
		const marks = rail.lanes.keyframes!.marks;
		expect(marks.map((m) => m.kind)).toEqual(['start']);
		expect(marks[0]).toMatchObject({ empty: true, label: 'START' });
		expect(rail.lanes.keyframes!.canAdd).toBe(false);
	});

	it('a mid-chain continuation shot (does not open fresh) draws no anchor at all', () => {
		// shot 2 (s2) continues in from shot 1, and its own outgoing join to
		// shot 3 is a hard cut -- `chainSegmentEdgeAllowances.leading[1]` is
		// false (doesn't open fresh) and `.trailing[1]` is false too (trailing
		// requires opensFresh AND closesFresh AND leadingEdgeAllowed AND
		// trailingEdgeAllowed -- opensFresh alone already kills it). No media
		// is set on it, so neither anchor renders.
		const rail = deriveShotRail(wanDoc(), wanCaps(), 's2');
		expect(rail.lanes.keyframes).not.toBeNull();
		const marks = rail.lanes.keyframes!.marks.filter((m) => m.kind === 'start' || m.kind === 'end');
		expect(marks).toEqual([]);
	});

	it('existing edge media on a shot the capability would otherwise deny an anchor to is never hidden', () => {
		// Mirrors railModel.ts's own film-level edge mirrors (`deriveChainRail`'s
		// `edgeKeyframes`), which push purely off `segment.keyframe`/
		// `last_keyframe` with no capability check at all -- a segment that
		// somehow already carries edge media (legacy/imported data, or caps
		// that changed after it was set) must keep its anchor.
		const doc = wanDoc();
		doc.chain.segments = doc.chain.segments.map((s) =>
			s.id === 's2' ? { ...s, keyframe: { path: 'legacy-start.png' }, last_keyframe: { path: 'legacy-end.png' } } : s
		);
		const rail = deriveShotRail(doc, wanCaps(), 's2');
		const marks = rail.lanes.keyframes!.marks;
		expect(marks.find((m) => m.kind === 'start')).toBeDefined();
		expect(marks.find((m) => m.kind === 'end')).toBeDefined();
	});
});

describe('deriveShotRail — chain routing (H3, anywhere: free keyframes + audio, rebased to shot-local time)', () => {
	it('a free chain keyframe landing inside shot 2 is rebased into shot 2\'s OWN generated timeline (including its 17f incoming overlap), not film/output time', () => {
		const doc = h3Doc();
		// shot 1 contributes [0, 5.8) of OUTPUT time; shot 2 contributes
		// [5.8, 5.8+184/25) after its 17f overlap deduction. Place a keyframe
		// 1.5s into shot 2's contributed (OUTPUT) window.
		doc.chain.keyframes = [{ id: 'ckf-1', at: 5.8 + 1.5, strength: 1, media: { path: 'x.png', url: 'https://x/x.png' } }];
		const rail1 = deriveShotRail(doc, h3Caps(), 'h1');
		const rail2 = deriveShotRail(doc, h3Caps(), 'h2');
		expect(rail1.lanes.keyframes!.marks.some((m) => m.id === 'ckf-1')).toBe(false);
		const mark = rail2.lanes.keyframes!.marks.find((m) => m.id === 'ckf-1')!;
		expect(mark).toBeDefined();
		expect(mark.kind).toBe('free');
		// localFrame = round(1.5s * 25fps) + 17f overlap = 38 + 17 = 55 -> 2.2s
		// into shot 2's OWN 201-frame generation (mirrors stageModel.ts's
		// chainLandingWindow precedent: a shot's own render starts
		// `overlapInFrames` earlier than what it contributes to the output).
		expect(mark.label).toBe('2.2 s');
		expect(mark.atPercent).toBeCloseTo((55 / 201) * 100, 3);
	});

	it('a chain audio clip spanning past a shot boundary is clipped and rebased to each shot it touches', () => {
		const doc = h3Doc();
		// Clip runs from 4s to 7s film time -- crosses the 5.8s seam between
		// shot 1 (0-5.8s) and shot 2 (5.8-13.84s).
		doc.chain.audio = [{ id: 'a1', start: 4, trim_start: 0, length: 3, media: { path: 'clip.wav', name: 'clip.wav' } }];
		const rail1 = deriveShotRail(doc, h3Caps(), 'h1');
		const rail2 = deriveShotRail(doc, h3Caps(), 'h2');
		const clip1 = rail1.lanes.audio!.clips.find((c) => c.id === 'a1')!;
		const clip2 = rail2.lanes.audio!.clips.find((c) => c.id === 'a1')!;
		expect(clip1).toBeDefined();
		expect(clip2).toBeDefined();
		// clip1 covers [4, 5.8) of shot 1's [0,5.8) span -> ends exactly at 100%
		expect(clip1.startPercent).toBeCloseTo((4 / 5.8) * 100, 1);
		expect(clip1.widthPercent + clip1.startPercent).toBeCloseTo(100, 1);
		// clip2 covers [5.8, 7) of shot 2's own span -> starts exactly at 0%
		expect(clip2.startPercent).toBeCloseTo(0, 1);
	});

	it('reaching the global keyframe cap disables every shot lane\'s canAdd', () => {
		const doc = h3Doc();
		doc.chain.keyframes = Array.from({ length: 8 }, (_, i) => ({ id: `k${i}`, at: i * 0.5, strength: 1, media: null }));
		const rail = deriveShotRail(doc, h3Caps(), 'h1');
		expect(rail.lanes.keyframes!.cap).toBe(8);
		expect(rail.lanes.keyframes!.canAdd).toBe(false);
	});

	it('below cap, canAdd stays true when free placement is active', () => {
		const rail = deriveShotRail(h3Doc(), h3Caps(), 'h1');
		expect(rail.lanes.keyframes!.canAdd).toBe(true);
	});

	// 09-04 maintainer bug report: clicking the START/END anchor selected
	// nothing and the stage stayed empty. Root cause was this file minting an
	// ad-hoc `${segment.id}-leading`/`-trailing` id for the anchor mark that
	// neither `parseChainEdgeKeyframeId` nor `doc.chain.keyframes` could ever
	// resolve -- `chainEdgeKeyframeId` is the one id both this rail mark and
	// `deriveStageModel`'s `buildKeyframeModel` (stageModel.ts) must agree on.
	it('the START/END anchor marks use chainEdgeKeyframeId, the same id deriveStageModel resolves', () => {
		const doc = h3Doc();
		doc.chain.segments = doc.chain.segments.map((s) =>
			s.id === 'h1' ? { ...s, keyframe: { path: 'start.png' }, last_keyframe: { path: 'end.png' } } : s
		);
		const rail = deriveShotRail(doc, h3Caps(), 'h1');
		const marks = rail.lanes.keyframes!.marks;
		const start = marks.find((m) => m.kind === 'start')!;
		const end = marks.find((m) => m.kind === 'end')!;
		expect(start.id).toBe(chainEdgeKeyframeId('first', 'h1'));
		expect(end.id).toBe(chainEdgeKeyframeId('last', 'h1'));
		expect(parseChainEdgeKeyframeId(start.id)).toEqual({ edge: 'first', segmentId: 'h1' });
		expect(parseChainEdgeKeyframeId(end.id)).toEqual({ edge: 'last', segmentId: 'h1' });
	});

	// Exact maintainer scenario (09-07): a fresh single-shot MiniMax-H3 `video`
	// document with no media and no audio must still offer locked, empty
	// START/END wells -- otherwise i2v/flf are unreachable.
	it('a fresh single-shot document (no media, no audio) still shows locked, empty START/END wells; canAdd unlocks once the doc grows a second shot', () => {
		const doc = h3Doc();
		doc.chain.segments = [doc.chain.segments[0]]; // just 'h1', no media, no audio
		const rail = deriveShotRail(doc, h3Caps(), 'h1');
		expect(rail.lanes.keyframes).not.toBeNull();
		const marks = rail.lanes.keyframes!.marks;
		expect(marks.map((m) => m.kind)).toEqual(['start', 'end']);
		expect(marks.every((m) => m.empty)).toBe(true);
		expect(rail.lanes.keyframes!.canAdd).toBe(false);

		const shaped = h3Doc(); // back to 3 segments -- director-shaped
		const shapedRail = deriveShotRail(shaped, h3Caps(), 'h1');
		expect(shapedRail.lanes.keyframes!.canAdd).toBe(true);
	});
});

describe('deriveShotRail — timeline routing (LTX): already shot-local, no rebasing', () => {
	it('spans the whole document and reports every prompt beat plus a Global gap-fill', () => {
		const rail = deriveShotRail(ltxDoc(), ltxCaps(), 'shot-1');
		expect(rail.durationSeconds).toBeCloseTo(8.6, 5);
		const beats = rail.lanes.prompt!.beats;
		expect(beats.map((b) => b.global)).toEqual([false, true, false]);
		expect(beats[1].text).toBe('Global');
		expect(rail.lanes.prompt!.canAdd).toBe(true);
	});

	it('a start/end/free keyframe is placed at film-time percent directly (no rebasing needed)', () => {
		const doc = ltxDoc();
		doc.timeline = {
			...doc.timeline,
			shots: [
				{
					...doc.timeline.shots[0],
					keyframes: [
						{ id: 'kf-first', start: 0, role: 'first', strength: 1, media: { path: 's.png', url: 'https://x/s.png' } },
						{ id: 'kf-free', start: 2.15, role: 'free', strength: 1, media: null },
						{ id: 'kf-last', start: 8.6, role: 'last', strength: 1, media: null }
					]
				}
			]
		};
		const rail = deriveShotRail(doc, ltxCaps(), 'shot-1');
		const marks = rail.lanes.keyframes!.marks;
		expect(marks.find((m) => m.id === 'kf-first')).toMatchObject({ kind: 'start', atPercent: 0, label: 'START', empty: false });
		expect(marks.find((m) => m.id === 'kf-last')).toMatchObject({ kind: 'end', atPercent: 100, label: 'END', empty: true });
		const free = marks.find((m) => m.id === 'kf-free')!;
		expect(free.kind).toBe('free');
		expect(free.label).toBe('2.15 s');
		expect(free.atPercent).toBeCloseTo((2.15 / 8.6) * 100, 3);
	});

	it('no per-shot generator cap on timeline routing: an unset maxKeyframes never disables canAdd', () => {
		const rail = deriveShotRail(ltxDoc(), ltxCaps(), 'shot-1');
		expect(rail.lanes.keyframes!.cap).toBeNull();
		expect(rail.lanes.keyframes!.canAdd).toBe(true);
	});
});
