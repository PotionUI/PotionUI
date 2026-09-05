import { describe, it, expect } from 'vitest';
import { deriveConsoleModel } from './consoleModel';
import { resolveDirectorCapabilities } from '$lib/utils/videoDirector';
import { directorShotInputIdentity, directorPredecessorOutputKey } from '$lib/utils/directorInputIdentity';
import type { VideoDirectorValue, DirectorCapabilities, DirectorModeCapability, ChainSegment } from '$lib/types/videoDirector';
import type { DirectorRunState } from '$lib/types/tabs';

// ─── Shared fixture helpers (mirror railModel.test.ts's own idiom) ─────────

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

// ─── Wan chain: 49+81+33 frames @16fps, one 16f continue then a hard cut ───

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
	doc.global_prompt = 'Continuous workshop pan';
	doc.chain.fps = 16;
	doc.chain.continuation = { overlap_frames: 16, stitch: true };
	doc.chain.segments = [
		chainSegment('s1', 'Lanterns, rain on paper lanterns', 49 / 16),
		chainSegment('s2', 'Steam swallows the frame', 81 / 16),
		chainSegment('s3', 'Across the street', 33 / 16, 't2v')
	];
	return doc;
}

// ─── H3 (video) chain: anywhere keyframes + audio, no continuation forced ──

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
		chainSegment('h1', 'Stall row', 145 / 25),
		chainSegment('h2', 'The wok', 201 / 25),
		chainSegment('h3', 'Looking up', 105 / 25)
	];
	return doc;
}

// ─── LTX timeline: three prompt beats + a free keyframe + start/end frames ─

function ltxCaps(): DirectorCapabilities {
	return {
		presetModes: null,
		modes: {
			director: baseModeCap({ audio: true, icLora: true, maxKeyframes: null, keyframes: 'anywhere' })
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
	doc.timeline = {
		fps: 25,
		shots: [
			{
				...doc.timeline.shots[0],
				duration: 40.04,
				segments: [
					{ id: 'b1', start: 0, end: 4.2, text: 'Rain on the neon sign', prompt_segments: [] },
					{ id: 'b2', start: 4.2, end: 8.6, text: 'Past the noodle window', prompt_segments: [] }
				]
			}
		]
	};
	return doc;
}

// ─── H3 refs: the full merged profile from a real preset_mode_overrides
// shape (mirrors stageModel.test.ts's own H3_REFS_PRESET_RAW fixture). ─────

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

describe('deriveConsoleModel — Wan chain', () => {
	const model = deriveConsoleModel(wanDoc(), wanCaps(), { activeShotId: null });

	it('renders one console shot per chain segment, numbered/faceted from the segment', () => {
		expect(model.shots).toHaveLength(3);
		expect(model.shots.map((s) => s.number)).toEqual(['01', '02', '03']);
		expect(model.shots[0].frames).toBe(49);
		expect(model.shots[1].frames).toBe(81);
		expect(model.shots[1].newFrames).toBe(65); // 81 - 16f overlap
		expect(model.shots[2].newFrames).toBeNull(); // hard cut, nothing inherited
	});

	it('cap chips: edge-anchors-only (first_only) + Per-shot LoRAs, no free-keyframe or audio chip', () => {
		const texts = model.header.capChips.map((c) => c.text);
		expect(texts).toContain('Edge anchors only');
		expect(texts).toContain('Per-shot LoRAs');
		expect(texts).not.toContain('First + last frame');
		expect(texts.some((t) => t.startsWith('Free keyframes'))).toBe(false);
		expect(texts).not.toContain('Audio');
	});

	it('join kinds: native continuation then hard cut, both with a toggle control (continuation available)', () => {
		expect(model.joins).toHaveLength(2);
		expect(model.joins[0].kind).toBe('native');
		expect(model.joins[0].overlapFrames).toBe(16);
		expect(model.joins[0].sentence).toBe("Inherits Shot 01's last frame · 16f overlap.");
		expect(model.joins[0].control).toEqual({ kind: 'toggle', value: 'continue' });
		expect(model.joins[1].kind).toBe('cut');
		expect(model.joins[1].sentence).toBe('Starts fresh — nothing shared.');
		expect(model.joins[1].control).toEqual({ kind: 'toggle', value: 'cut' });
	});

	it('badge: continuous on the outgoing side of a continue join, needs-previous on the incoming side with no runs map (W3), independent where both joins are cuts', () => {
		expect(model.shots[0].badge).toBe('continuous'); // outgoing continue -- not itself dependent on anything
		// s2 DEPENDS on s1's output (incoming continue) -- with no runs map at
		// all, that dependency is unmet (see the dedicated W3 badge describe
		// block below for the full needs-previous/input-ready/stale/continuous
		// state machine once a runs map exists).
		expect(model.shots[1].badge).toBe('needs-previous');
		expect(model.shots[2].badge).toBe('independent'); // both joins around it are cuts
	});

	it('LoRAs tab is present (perSegmentLoras); References/IC-LoRA tabs are not', () => {
		const tabIds = model.shots[0].tabs.map((t) => t.id);
		expect(tabIds).toEqual(['selection', 'loras']);
		expect(model.shots[0].tabs[1].label).toBe('LoRAs · 0');
	});

	it('a chain shot never carries IC-LoRA (timeline-only field)', () => {
		expect(model.shots.every((s) => s.hasIcLora === false && s.icLoraCount === 0)).toBe(true);
	});

	it('LTX-only "Add shot" gate does not apply to chain routing: canAddShot mirrors the rail', () => {
		expect(model.canAddShot).toBe(true);
		expect(model.addShotDisabledReason).toBeNull();
	});

	it('thumb precedence keyframe > start > end > slate: a free keyframe landing in shot 2 wins over its own well', () => {
		const doc = wanDoc();
		doc.chain.segments[1].keyframe = { path: 'shot2-start.png', url: 'https://x/shot2-start.png' };
		doc.chain.keyframes = [{ id: 'ckf-1', at: doc.chain.segments[0].duration + 0.1, strength: 1, media: { path: 'landing.png', url: 'https://x/landing.png' } }];
		const withKf = deriveConsoleModel(doc, wanCaps(), { activeShotId: null });
		expect(withKf.shots[1].thumb).toEqual({ url: 'https://x/landing.png', source: 'keyframe' });

		const doc2 = wanDoc();
		doc2.chain.segments[1].keyframe = { path: 'shot2-start.png', url: 'https://x/shot2-start.png' };
		const withStart = deriveConsoleModel(doc2, wanCaps(), { activeShotId: null });
		expect(withStart.shots[1].thumb).toEqual({ url: 'https://x/shot2-start.png', source: 'start' });

		const doc3 = wanDoc();
		doc3.chain.segments[1].last_keyframe = { path: 'shot2-end.png', url: 'https://x/shot2-end.png' };
		const withEnd = deriveConsoleModel(doc3, wanCaps(), { activeShotId: null });
		expect(withEnd.shots[1].thumb).toEqual({ url: 'https://x/shot2-end.png', source: 'end' });

		const bare = deriveConsoleModel(wanDoc(), wanCaps(), { activeShotId: null });
		expect(bare.shots[1].thumb).toEqual({ url: null, source: 'slate' });
	});
});

describe('deriveConsoleModel — MiniMax-H3 Video chain (anywhere keyframes + audio)', () => {
	it('draws the free-keyframe and Audio cap chips, no LoRAs chip', () => {
		const model = deriveConsoleModel(h3Doc(), h3Caps(), { activeShotId: null });
		const texts = model.header.capChips.map((c) => c.text);
		expect(texts).toContain('First + last frame');
		expect(texts).toContain('Free keyframes · 0/8');
		expect(texts).toContain('Audio');
		expect(texts).not.toContain('Per-shot LoRAs');
	});

	it('counts placed anywhere-keyframes into the header chip', () => {
		const doc = h3Doc();
		doc.chain.keyframes = [{ id: 'k1', at: 1, strength: 1, media: null }];
		const model = deriveConsoleModel(doc, h3Caps(), { activeShotId: null });
		expect(model.header.capChips.find((c) => c.text.startsWith('Free keyframes'))?.text).toBe('Free keyframes · 1/8');
	});
});

describe('deriveConsoleModel — LTX timeline (W2: one console shot per DirectorTimelineShot)', () => {
	const model = deriveConsoleModel(ltxDoc(), ltxCaps(), { activeShotId: null });

	it('renders exactly one shot for a single-shot document, id = the real shot id', () => {
		expect(model.shots).toHaveLength(1);
		expect(model.shots[0].id).toBe('shot-1');
		expect(model.header.shotCount).toBe(1);
		expect(model.shots[0].durationSeconds).toBeCloseTo(8.6, 5);
		expect(model.shots[0].frames).toBe(Math.round(8.6 * 25));
		expect(model.shots[0].capFrames).toBe(1001);
	});

	it('always offers "Add shot" for timeline routing (W2: multiple independent LTX shots)', () => {
		expect(model.canAddShot).toBe(true);
		expect(model.addShotDisabledReason).toBeNull();
	});

	it('no joins exist for a single-shot timeline document', () => {
		expect(model.joins).toEqual([]);
	});

	it('badge is independent when the shot has no continue_from_previous', () => {
		expect(model.shots[0].badge).toBe('independent');
	});

	it('a second shot adds a continue/cut join; its badge needs the predecessor\'s run (W3, see the dedicated describe block below) — with none, needs-previous', () => {
		const doc = ltxDoc();
		doc.timeline = {
			...doc.timeline,
			shots: [...doc.timeline.shots, { id: 'shot-2', duration: 3, continue_from_previous: true, segments: [], keyframes: [], audio: [], ic_lora: [] }]
		};
		const withSecond = deriveConsoleModel(doc, ltxCaps(), { activeShotId: null });
		expect(withSecond.shots).toHaveLength(2);
		expect(withSecond.header.shotCount).toBe(2);
		expect(withSecond.shots[1].badge).toBe('needs-previous');
		expect(withSecond.joins).toHaveLength(1);
		expect(withSecond.joins[0]).toMatchObject({ afterShotId: 'shot-1', beforeShotId: 'shot-2', kind: 'continue' });
	});

	it('IC-LoRA tab/chip reflect that SHOT\'s own ic_lora list, not the whole document', () => {
		const doc = ltxDoc();
		doc.timeline = {
			...doc.timeline,
			shots: [{ ...doc.timeline.shots[0], ic_lora: [{ id: 'icl-1', lora: { model: 'style', strength: 0.8 }, ref_media: null, strength: 1 }] }]
		};
		const withIc = deriveConsoleModel(doc, ltxCaps(), { activeShotId: null });
		expect(withIc.header.capChips.map((c) => c.text)).toContain('IC-LoRA');
		expect(withIc.shots[0].hasIcLora).toBe(true);
		expect(withIc.shots[0].icLoraCount).toBe(1);
		expect(withIc.shots[0].tabs.map((t) => t.id)).toEqual(['selection', 'ic_lora']);
		expect(withIc.shots[0].tabs[1].label).toBe('IC-LoRA · 1');
	});

	it('thumb precedence keyframe > start > end > slate', () => {
		const doc = ltxDoc();
		doc.timeline = {
			...doc.timeline,
			shots: [
				{
					...doc.timeline.shots[0],
					keyframes: [
						{ id: 'kf-first', start: 0, role: 'first', strength: 1, media: { path: 'start.png', url: 'https://x/start.png' } },
						{ id: 'kf-last', start: 8.6, role: 'last', strength: 1, media: { path: 'end.png', url: 'https://x/end.png' } }
					]
				}
			]
		};
		const withEdges = deriveConsoleModel(doc, ltxCaps(), { activeShotId: null });
		expect(withEdges.shots[0].thumb).toEqual({ url: 'https://x/start.png', source: 'start' });

		const docWithFree = {
			...doc,
			timeline: {
				...doc.timeline,
				shots: [
					{
						...doc.timeline.shots[0],
						keyframes: [
							...doc.timeline.shots[0].keyframes,
							{ id: 'kf-free', start: 2, role: 'free' as const, strength: 1, media: { path: 'free.png', url: 'https://x/free.png' } }
						]
					}
				]
			}
		};
		const withFree = deriveConsoleModel(docWithFree, ltxCaps(), { activeShotId: null });
		expect(withFree.shots[0].thumb).toEqual({ url: 'https://x/free.png', source: 'keyframe' });

		const bare = deriveConsoleModel(ltxDoc(), ltxCaps(), { activeShotId: null });
		expect(bare.shots[0].thumb).toEqual({ url: null, source: 'slate' });
	});
});

describe('deriveConsoleModel — H3 refs (continuationDisabled + per_shot references)', () => {
	const refsCaps = resolveDirectorCapabilities(H3_REFS_PRESET_RAW, 'refs')!;

	it('every join is a permanent cut rendered as a chip, never a toggle', () => {
		const doc = wanDoc();
		doc.chain.fps = 25;
		doc.chain.continuation = { overlap_frames: 17, stitch: true };
		const model = deriveConsoleModel(doc, refsCaps, { activeShotId: null });
		expect(model.joins.length).toBeGreaterThan(0);
		for (const join of model.joins) {
			expect(join.kind).toBe('cut');
			expect(join.control).toEqual({ kind: 'chip', text: 'Independent' });
		}
	});

	it('References tab appears on every shot (per_shot capability)', () => {
		const doc = wanDoc();
		const model = deriveConsoleModel(doc, refsCaps, { activeShotId: null });
		expect(model.shots.every((s) => s.tabs.some((t) => t.id === 'references'))).toBe(true);
		expect(model.shots[0].tabs.find((t) => t.id === 'references')?.label).toBe('References · All');
	});

	it('a per-shot selection is counted against the live form pool', () => {
		const doc = wanDoc();
		doc.chain.segments[0].references = [{ path: '/pool/a.png' }];
		const formData = { references: [{ path: '/pool/a.png' }, { path: '/pool/b.png' }] };
		const model = deriveConsoleModel(doc, refsCaps, { activeShotId: null }, formData);
		expect(model.shots[0].tabs.find((t) => t.id === 'references')?.label).toBe('References · 1 of 2');
	});
});

// ─── W3: run states, dependency badges, missing-predecessor joins ─────────

function run(overrides: Partial<DirectorRunState> = {}): DirectorRunState {
	return {
		generationId: 'gen-1',
		status: 'queued',
		progress: null,
		finishedAt: null,
		posterUrl: null,
		inputsHash: null,
		...overrides
	};
}

describe('deriveConsoleModel — W3 run states', () => {
	it('run is null with no runs map, matching W1/W2 exactly', () => {
		const model = deriveConsoleModel(wanDoc(), wanCaps(), { activeShotId: null });
		expect(model.shots.every((s) => s.run === null)).toBe(true);
	});

	it('maps queued/generating/done/failed onto the shot with the matching id', () => {
		const doc = wanDoc();
		const runs: Record<string, DirectorRunState> = {
			s1: run({ status: 'queued' }),
			s2: run({ status: 'generating', progress: 0.417 }),
			s3: run({ status: 'failed' })
		};
		const model = deriveConsoleModel(doc, wanCaps(), { activeShotId: null }, null, runs);
		expect(model.shots[0].run).toEqual({ kind: 'queued' });
		expect(model.shots[1].run).toEqual({ kind: 'generating', percent: 42 });
		expect(model.shots[2].run).toEqual({ kind: 'failed' });
	});

	it('done reads as HH:MM off finishedAt, and its output poster replaces the row thumb', () => {
		const doc = wanDoc();
		const finishedAt = new Date(2026, 0, 1, 9, 5).getTime();
		const runs: Record<string, DirectorRunState> = {
			s1: run({ status: 'done', finishedAt, posterUrl: 'https://x/out.mp4' })
		};
		const model = deriveConsoleModel(doc, wanCaps(), { activeShotId: null }, null, runs);
		expect(model.shots[0].run).toEqual({ kind: 'done', time: '09:05' });
		expect(model.shots[0].thumb).toEqual({ url: 'https://x/out.mp4', source: 'output' });
	});

	it('a run that is not done never overrides the shot\'s own editor-side thumb', () => {
		const doc = wanDoc();
		const runs: Record<string, DirectorRunState> = { s1: run({ status: 'generating', progress: 0.5, posterUrl: null }) };
		const model = deriveConsoleModel(doc, wanCaps(), { activeShotId: null }, null, runs);
		expect(model.shots[0].thumb).toEqual({ url: null, source: 'slate' });
	});
});

describe('deriveConsoleModel — W3 dependency badges (chain: continue join between s1 and s2)', () => {
	it('needs-previous when the predecessor has no run at all', () => {
		const model = deriveConsoleModel(wanDoc(), wanCaps(), { activeShotId: null }, null, {});
		expect(model.shots[1].badge).toBe('needs-previous');
	});

	it('needs-previous when the predecessor run exists but is not done', () => {
		const runs: Record<string, DirectorRunState> = { s1: run({ status: 'generating', progress: 0.5 }) };
		const model = deriveConsoleModel(wanDoc(), wanCaps(), { activeShotId: null }, null, runs);
		expect(model.shots[1].badge).toBe('needs-previous');
	});

	it('still needs-previous (not input-ready) when the predecessor is done but this shot has never itself rendered -- native chain has no reusable handoff (today), so generating this shot alone would resubmit it as a rejected span start', () => {
		const runs: Record<string, DirectorRunState> = { s1: run({ status: 'done', finishedAt: 1000 }) };
		const model = deriveConsoleModel(wanDoc(), wanCaps(), { activeShotId: null }, null, runs);
		expect(model.shots[1].badge).toBe('needs-previous');
	});

	it('continuous when both are done with a complete identity and the predecessor has not changed since', () => {
		const doc = wanDoc();
		const s1Hash = directorShotInputIdentity(doc, 's1', { caps: wanCaps(), formData: null });
		const runs: Record<string, DirectorRunState> = {
			s1: run({ status: 'done', finishedAt: 1000, inputsHash: s1Hash, generationId: 'gen-1' }),
			s2: run({ status: 'done', finishedAt: 2000, predecessorRef: { generationId: 'gen-1', outputKey: directorPredecessorOutputKey(wanCaps(), 's1') } })
		};
		const model = deriveConsoleModel(doc, wanCaps(), { activeShotId: null }, null, runs);
		expect(model.shots[1].badge).toBe('continuous');
	});

	it('stale when the predecessor was regenerated under a new generation since this run\'s predecessorRef was stamped, even with identical inputs', () => {
		const doc = wanDoc();
		const s1Hash = directorShotInputIdentity(doc, 's1', { caps: wanCaps(), formData: null });
		const runs: Record<string, DirectorRunState> = {
			// s1's CURRENT run is gen-1-retry -- s2 was submitted against the
			// earlier gen-1, so its stamped predecessorRef no longer matches,
			// even though s1's document content itself never changed.
			s1: run({ status: 'done', finishedAt: 3000, inputsHash: s1Hash, generationId: 'gen-1-retry' }),
			s2: run({ status: 'done', finishedAt: 2000, predecessorRef: { generationId: 'gen-1', outputKey: directorPredecessorOutputKey(wanCaps(), 's1') } })
		};
		const model = deriveConsoleModel(doc, wanCaps(), { activeShotId: null }, null, runs);
		expect(model.shots[1].badge).toBe('stale');
	});

	it('stale when the predecessor\'s live document changed since its own run (edited, not re-rendered)', () => {
		const doc = wanDoc();
		const staleHash = directorShotInputIdentity(doc, 's1', { caps: wanCaps(), formData: null }) + '-old';
		const runs: Record<string, DirectorRunState> = {
			s1: run({ status: 'done', finishedAt: 1000, inputsHash: staleHash, generationId: 'gen-1' }),
			s2: run({ status: 'done', finishedAt: 2000, predecessorRef: { generationId: 'gen-1', outputKey: directorPredecessorOutputKey(wanCaps(), 's1') } })
		};
		const model = deriveConsoleModel(doc, wanCaps(), { activeShotId: null }, null, runs);
		expect(model.shots[1].badge).toBe('stale');
	});

	it('unverified when the predecessor\'s stored inputsHash predates versioned identities (old fingerprint format)', () => {
		const doc = wanDoc();
		const runs: Record<string, DirectorRunState> = {
			// A bare JSON.stringify, the retired directorShotFingerprint's shape -- no version prefix.
			s1: run({ status: 'done', finishedAt: 1000, inputsHash: JSON.stringify(doc.chain.segments[0]), generationId: 'gen-1' }),
			s2: run({ status: 'done', finishedAt: 2000, predecessorRef: { generationId: 'gen-1', outputKey: directorPredecessorOutputKey(wanCaps(), 's1') } })
		};
		const model = deriveConsoleModel(doc, wanCaps(), { activeShotId: null }, null, runs);
		expect(model.shots[1].badge).toBe('unverified');
	});

	it('unverified when this shot\'s own run has no predecessorRef (an old stored session)', () => {
		const doc = wanDoc();
		const s1Hash = directorShotInputIdentity(doc, 's1', { caps: wanCaps(), formData: null });
		const runs: Record<string, DirectorRunState> = {
			s1: run({ status: 'done', finishedAt: 1000, inputsHash: s1Hash, generationId: 'gen-1' }),
			s2: run({ status: 'done', finishedAt: 2000 }) // predecessorRef absent
		};
		const model = deriveConsoleModel(doc, wanCaps(), { activeShotId: null }, null, runs);
		expect(model.shots[1].badge).toBe('unverified');
	});

	it('a shot with no dependency (both its joins are cuts) never consults runs', () => {
		const model = deriveConsoleModel(wanDoc(), wanCaps(), { activeShotId: null }, null, {});
		expect(model.shots[2].badge).toBe('independent');
	});
});

describe('deriveConsoleModel — W3 missing-predecessor join (chain)', () => {
	it('unchecked: a broken continuation still renders as a normal toggle, never the warning block', () => {
		const model = deriveConsoleModel(wanDoc(), wanCaps(), { activeShotId: null }, null, {}, new Set());
		expect(model.joins[0].kind).toBe('native');
		expect(model.joins[0].control.kind).toBe('toggle');
	});

	it('checked + predecessor has no done run: the join becomes "missing" with the simple 2-shot span', () => {
		const model = deriveConsoleModel(wanDoc(), wanCaps(), { activeShotId: null }, null, {}, new Set(['s2']));
		expect(model.joins[0]).toMatchObject({
			kind: 'missing',
			label: 'MISSING PREDECESSOR',
			sentence: 'Shot 01 has no output yet.'
		});
		expect(model.joins[0].control).toEqual({ kind: 'missing', spanShotIds: ['s1', 's2'] });
	});

	it('checked + predecessor IS done but not itself checked: still "missing" -- a done run is not a reusable native handoff, so submitting s2 alone would resubmit it as a rejected span start', () => {
		const runs: Record<string, DirectorRunState> = { s1: run({ status: 'done', finishedAt: 1000 }) };
		const model = deriveConsoleModel(wanDoc(), wanCaps(), { activeShotId: null }, null, runs, new Set(['s2']));
		expect(model.joins[0]).toMatchObject({ kind: 'missing' });
		expect(model.joins[0].control).toEqual({ kind: 'missing', spanShotIds: ['s1', 's2'] });
	});

	it('checked covers the whole span itself (s1 and s2 both checked): no warning, normal toggle, regardless of run state', () => {
		const model = deriveConsoleModel(wanDoc(), wanCaps(), { activeShotId: null }, null, {}, new Set(['s1', 's2']));
		expect(model.joins[0].kind).toBe('native');
	});

	it('checking the far shot alone never flags the earlier, unrelated cut join', () => {
		const model = deriveConsoleModel(wanDoc(), wanCaps(), { activeShotId: null }, null, {}, new Set(['s3']));
		expect(model.joins[1].kind).toBe('cut'); // s2->s3 is a hard cut regardless of checked state
	});

	it('a checked shot with a broken CHAIN of continuations spans back through both', () => {
		const doc = wanDoc();
		// s3 was a hard cut ('t2v' override) -- flip it to continue s2 so checking
		// s3 alone must span all the way back to s1 (the nearest fresh cut).
		doc.chain.segments[2].sub_type_override = null;
		const model = deriveConsoleModel(doc, wanCaps(), { activeShotId: null }, null, {}, new Set(['s3']));
		expect(model.joins[1]).toMatchObject({ kind: 'missing' });
		expect(model.joins[1].control).toEqual({ kind: 'missing', spanShotIds: ['s1', 's2', 's3'] });
	});

	it('multi-hop: checking s2 and s3 but not s1 still leaves BOTH joins "missing" -- s2 itself still needs s1 behind it', () => {
		const doc = wanDoc();
		doc.chain.segments[2].sub_type_override = null; // s3 continues s2, same fixture as above
		const model = deriveConsoleModel(doc, wanCaps(), { activeShotId: null }, null, {}, new Set(['s2', 's3']));
		expect(model.joins[0]).toMatchObject({ kind: 'missing' });
		expect(model.joins[1]).toMatchObject({ kind: 'missing' });
	});

	it('multi-hop: checking s1, s2 and s3 together covers the whole span -- neither join is "missing"', () => {
		const doc = wanDoc();
		doc.chain.segments[2].sub_type_override = null;
		const model = deriveConsoleModel(doc, wanCaps(), { activeShotId: null }, null, {}, new Set(['s1', 's2', 's3']));
		expect(model.joins[0].kind).not.toBe('missing');
		expect(model.joins[1].kind).not.toBe('missing');
	});
});

describe('deriveConsoleModel — W3 dependency badge/missing-join (LTX timeline continue_from_previous)', () => {
	function ltxTwoShots(continueFromPrevious: boolean) {
		const doc = ltxDoc();
		doc.timeline = {
			...doc.timeline,
			shots: [
				doc.timeline.shots[0],
				{ id: 'shot-2', duration: 3, continue_from_previous: continueFromPrevious, segments: [], keyframes: [], audio: [], ic_lora: [] }
			]
		};
		return doc;
	}

	it('needs-previous with no runs map', () => {
		const model = deriveConsoleModel(ltxTwoShots(true), ltxCaps(), { activeShotId: null }, null, {});
		expect(model.shots[1].badge).toBe('needs-previous');
	});

	it('input-ready once the predecessor shot is done', () => {
		const runs: Record<string, DirectorRunState> = { 'shot-1': run({ status: 'done', finishedAt: 1000 }) };
		const model = deriveConsoleModel(ltxTwoShots(true), ltxCaps(), { activeShotId: null }, null, runs);
		expect(model.shots[1].badge).toBe('input-ready');
	});

	it('checked + predecessor not done renders the missing-predecessor join', () => {
		const model = deriveConsoleModel(ltxTwoShots(true), ltxCaps(), { activeShotId: null }, null, {}, new Set(['shot-2']));
		expect(model.joins[0]).toMatchObject({ kind: 'missing', sentence: 'Shot 01 has no output yet.' });
		expect(model.joins[0].control).toEqual({ kind: 'missing', spanShotIds: ['shot-1', 'shot-2'] });
	});

	it('a hard-cut join is never reported as missing regardless of checked/runs', () => {
		const model = deriveConsoleModel(ltxTwoShots(false), ltxCaps(), { activeShotId: null }, null, {}, new Set(['shot-2']));
		expect(model.joins[0].kind).toBe('cut');
	});
});
