import { describe, it, expect } from 'vitest';
import { deriveShotRail, buildRailTicks, railTimeFromFraction } from './shotRailModel';
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
	doc.timeline.fps = 25;
	doc.timeline.duration = 40.04;
	doc.timeline.segments = [
		{ id: 'b1', start: 0, end: 4.2, text: 'Rain on the neon sign', prompt_segments: [] },
		{ id: 'b2', start: 5.5, end: 8.6, text: 'Past the noodle window', prompt_segments: [] } // gap 4.2-5.5 -> global fill
	];
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

	it('no keyframes/audio lane at all under first_only + no audio capability', () => {
		const rail = deriveShotRail(wanDoc(), wanCaps(), 's1');
		expect(rail.lanes.keyframes).toBeNull();
		expect(rail.lanes.audio).toBeNull();
	});

	it('START/END marks still render even with no capability well (locked edge mirrors)', () => {
		// first_only DOES gate rail.lanes.keyframes false when there is no media
		// at all (railModel's own gating) -- confirm shot 1 (which HAS caps for
		// a leading well) still gets nothing until keyframes lane opens via caps.
		const caps = { ...wanCaps(), modes: { director: { ...wanCaps().modes.director!, keyframes: 'first_only' as const } } };
		const rail = deriveShotRail(wanDoc(), caps, 's1');
		// first_only never opens the keyframes LANE unless media already exists
		// (railModel.ts: lanes.keyframes = body.keyframes.length>0 || freePlacementActive)
		expect(rail.lanes.keyframes).toBeNull();
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
});

describe('deriveShotRail — timeline routing (LTX): already shot-local, no rebasing', () => {
	it('spans the whole document and reports every prompt beat plus a Global gap-fill', () => {
		const rail = deriveShotRail(ltxDoc(), ltxCaps(), 'timeline-shot');
		expect(rail.durationSeconds).toBeCloseTo(8.6, 5);
		const beats = rail.lanes.prompt!.beats;
		expect(beats.map((b) => b.global)).toEqual([false, true, false]);
		expect(beats[1].text).toBe('Global');
		expect(rail.lanes.prompt!.canAdd).toBe(true);
	});

	it('a start/end/free keyframe is placed at film-time percent directly (no rebasing needed)', () => {
		const doc = ltxDoc();
		doc.timeline.keyframes = [
			{ id: 'kf-first', start: 0, role: 'first', strength: 1, media: { path: 's.png', url: 'https://x/s.png' } },
			{ id: 'kf-free', start: 2.15, role: 'free', strength: 1, media: null },
			{ id: 'kf-last', start: 8.6, role: 'last', strength: 1, media: null }
		];
		const rail = deriveShotRail(doc, ltxCaps(), 'timeline-shot');
		const marks = rail.lanes.keyframes!.marks;
		expect(marks.find((m) => m.id === 'kf-first')).toMatchObject({ kind: 'start', atPercent: 0, label: 'START', empty: false });
		expect(marks.find((m) => m.id === 'kf-last')).toMatchObject({ kind: 'end', atPercent: 100, label: 'END', empty: true });
		const free = marks.find((m) => m.id === 'kf-free')!;
		expect(free.kind).toBe('free');
		expect(free.label).toBe('2.15 s');
		expect(free.atPercent).toBeCloseTo((2.15 / 8.6) * 100, 3);
	});

	it('no per-shot generator cap on timeline routing: an unset maxKeyframes never disables canAdd', () => {
		const rail = deriveShotRail(ltxDoc(), ltxCaps(), 'timeline-shot');
		expect(rail.lanes.keyframes!.cap).toBeNull();
		expect(rail.lanes.keyframes!.canAdd).toBe(true);
	});
});
