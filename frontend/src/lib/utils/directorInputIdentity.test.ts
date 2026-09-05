import { describe, it, expect } from 'vitest';
import { directorShotInputIdentity, directorPredecessorShotId, hasVersionedShotIdentity } from './directorInputIdentity';
import type {
	VideoDirectorValue,
	DirectorCapabilities,
	DirectorModeCapability,
	ChainSegment,
	DirectorTimelineShot
} from '$lib/types/videoDirector';

// ─── Fixture helpers (mirror consoleModel.test.ts's own idiom) ────────────

function modeCap(overrides: Partial<DirectorModeCapability> = {}): DirectorModeCapability {
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

function chainCaps(overrides: Partial<DirectorCapabilities> = {}): DirectorCapabilities {
	return {
		presetModes: null,
		modes: { director: modeCap({ keyframes: 'anywhere', audio: true, continuation: { source: 'tail_frames', overlapFrames: 16, stitch: true } }) },
		enabledModes: ['director'],
		defaultDuration: 5,
		defaultFps: 16,
		maxDuration: null,
		maxFrames: null,
		segmentRouting: true,
		references: null,
		referenceFields: [],
		...overrides
	};
}

function timelineCaps(overrides: Partial<DirectorCapabilities> = {}): DirectorCapabilities {
	return {
		presetModes: null,
		modes: { director: modeCap() },
		enabledModes: ['director'],
		defaultDuration: 5,
		defaultFps: 24,
		maxDuration: null,
		maxFrames: null,
		segmentRouting: false,
		references: null,
		referenceFields: [],
		...overrides
	};
}

function chainSegment(id: string, overrides: Partial<ChainSegment> = {}): ChainSegment {
	return {
		id,
		prompt: 'a shot',
		prompt_segments: [],
		duration: 3,
		loras: null,
		keyframe: null,
		keyframe_strength: 1,
		last_keyframe: null,
		last_keyframe_strength: 1,
		steps: null,
		cfg: null,
		sub_type_override: null,
		...overrides
	};
}

function timelineShot(id: string, overrides: Partial<DirectorTimelineShot> = {}): DirectorTimelineShot {
	return {
		id,
		duration: 5,
		continue_from_previous: false,
		segments: [],
		keyframes: [],
		audio: [],
		ic_lora: [],
		...overrides
	};
}

function baseValue(overrides: Partial<VideoDirectorValue> = {}): VideoDirectorValue {
	return {
		schema_version: 1,
		mode: 'director',
		global_prompt: 'a workshop',
		global_prompt_segments: [],
		negative_prompt: 'blurry',
		negative_prompt_segments: [],
		simple: { duration: 5, fps: 24, start_image: null, first_frame: null, last_frame: null },
		timeline: { fps: 24, shots: [timelineShot('t1')] },
		chain: {
			fps: 16,
			segments: [chainSegment('s1', { sub_type_override: 't2v' }), chainSegment('s2')],
			continuation: { overlap_frames: 16, stitch: true },
			keyframes: [],
			audio: []
		},
		...overrides
	};
}

// ─── Determinism / own-shot scope ──────────────────────────────────────────

describe('directorShotInputIdentity', () => {
	it('is byte-identical across two calls on an unchanged document', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const a = directorShotInputIdentity(doc, 's2', { caps, formData: null });
		const b = directorShotInputIdentity(doc, 's2', { caps, formData: null });
		expect(a).not.toBeNull();
		expect(a).toBe(b);
	});

	it('is versioned distinctly from a plain JSON.stringify (the retired fingerprint format)', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const identity = directorShotInputIdentity(doc, 's2', { caps, formData: null });
		expect(hasVersionedShotIdentity(identity)).toBe(true);
		expect(hasVersionedShotIdentity(JSON.stringify(doc.chain.segments[1]))).toBe(false);
		expect(hasVersionedShotIdentity(null)).toBe(false);
		expect(hasVersionedShotIdentity(undefined)).toBe(false);
	});

	it('returns null for a shot id the document no longer has', () => {
		const doc = baseValue();
		expect(directorShotInputIdentity(doc, 'ghost', { caps: chainCaps(), formData: null })).toBeNull();
	});

	it('changes when the shot\'s own field changes', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const before = directorShotInputIdentity(doc, 's2', { caps, formData: null });
		const edited = baseValue();
		edited.chain.segments[1] = { ...edited.chain.segments[1], prompt: 'a different shot' };
		const after = directorShotInputIdentity(edited, 's2', { caps, formData: null });
		expect(after).not.toBe(before);
	});

	it('editing an UNRELATED independent shot does not change another shot\'s identity', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const before = directorShotInputIdentity(doc, 's1', { caps, formData: null });
		const edited = baseValue();
		edited.chain.segments[1] = { ...edited.chain.segments[1], prompt: 'a totally different s2' };
		const after = directorShotInputIdentity(edited, 's1', { caps, formData: null });
		expect(after).toBe(before);
	});

	it('UI-only state (selection/collapsed/hover) never changes any shot\'s identity', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const before = directorShotInputIdentity(doc, 's2', { caps, formData: null });
		const withUi = baseValue({ ui: { activeShotId: 's2' } as VideoDirectorValue['ui'] });
		const after = directorShotInputIdentity(withUi, 's2', { caps, formData: null });
		expect(after).toBe(before);
	});

	// ─── Film-level fields the submission builders fold into every shot ───────

	it('changes when the film-wide global prompt changes', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const before = directorShotInputIdentity(doc, 's2', { caps, formData: null });
		const after = directorShotInputIdentity(baseValue({ global_prompt: 'a different film entirely' }), 's2', {
			caps,
			formData: null
		});
		expect(after).not.toBe(before);
	});

	it('changes when the film-wide negative prompt changes', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const before = directorShotInputIdentity(doc, 's2', { caps, formData: null });
		const after = directorShotInputIdentity(baseValue({ negative_prompt: 'extra limbs' }), 's2', { caps, formData: null });
		expect(after).not.toBe(before);
	});

	it('changes when chain fps changes (chain routing reads the film-level chain.fps, not a per-segment field)', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const before = directorShotInputIdentity(doc, 's2', { caps, formData: null });
		const edited = baseValue();
		edited.chain.fps = 24;
		const after = directorShotInputIdentity(edited, 's2', { caps, formData: null });
		expect(after).not.toBe(before);
	});

	it('changes when timeline fps changes for a timeline shot (timeline.fps is film-level, not per-shot)', () => {
		const doc = baseValue();
		const caps = timelineCaps();
		const before = directorShotInputIdentity(doc, 't1', { caps, formData: null });
		const edited = baseValue();
		edited.timeline.fps = 30;
		const after = directorShotInputIdentity(edited, 't1', { caps, formData: null });
		expect(after).not.toBe(before);
	});

	it('changes when a chain-wide "anywhere" free keyframe is added, for EVERY segment (shared across the whole chain generation)', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const beforeS1 = directorShotInputIdentity(doc, 's1', { caps, formData: null });
		const beforeS2 = directorShotInputIdentity(doc, 's2', { caps, formData: null });
		const edited = baseValue();
		edited.chain.keyframes = [{ id: 'kf-1', at: 1.5, strength: 1, media: { path: '/x.png' } }];
		expect(directorShotInputIdentity(edited, 's1', { caps, formData: null })).not.toBe(beforeS1);
		expect(directorShotInputIdentity(edited, 's2', { caps, formData: null })).not.toBe(beforeS2);
	});

	it('ignores an "anywhere" free keyframe when the mode does not declare that capability', () => {
		const caps = chainCaps({ modes: { director: modeCap({ keyframes: 'first_only' }) } });
		const doc = baseValue();
		const before = directorShotInputIdentity(doc, 's2', { caps, formData: null });
		const edited = baseValue();
		edited.chain.keyframes = [{ id: 'kf-1', at: 1.5, strength: 1, media: { path: '/x.png' } }];
		expect(directorShotInputIdentity(edited, 's2', { caps, formData: null })).toBe(before);
	});

	it('changes when chain-wide audio changes', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const before = directorShotInputIdentity(doc, 's2', { caps, formData: null });
		const edited = baseValue();
		edited.chain.audio = [{ id: 'a-1', start: 0, trim_start: 0, length: 3, media: { path: '/a.wav' } }];
		expect(directorShotInputIdentity(edited, 's2', { caps, formData: null })).not.toBe(before);
	});

	// ─── form_ref media: resolved identity, not the pointer literal ───────────

	it('changes when a form_ref keyframe resolves to a DIFFERENT underlying file even though the pointer itself is unchanged', () => {
		const doc = baseValue();
		doc.chain.segments[1] = { ...doc.chain.segments[1], keyframe: { form_ref: { field: 'reference_image', path: 'old.png' } } };
		const caps = chainCaps();
		const before = directorShotInputIdentity(doc, 's2', { caps, formData: { reference_image: { path: 'old.png' } } });
		// The pointer's {field, path} literal never changes -- only what the
		// form field now actually holds does (a replace/reorder scenario the
		// retired fingerprint, a bare JSON.stringify of the segment, could
		// never see).
		const after = directorShotInputIdentity(doc, 's2', { caps, formData: { reference_image: { path: 'new.png' } } });
		expect(after).not.toBe(before);
	});

	it('a form_ref that becomes unresolvable (broken) is a change from when it resolved', () => {
		const doc = baseValue();
		doc.chain.segments[1] = { ...doc.chain.segments[1], keyframe: { form_ref: { field: 'reference_image', path: 'old.png' } } };
		const caps = chainCaps();
		const resolved = directorShotInputIdentity(doc, 's2', { caps, formData: { reference_image: { path: 'old.png' } } });
		const broken = directorShotInputIdentity(doc, 's2', { caps, formData: {} });
		expect(broken).not.toBe(resolved);
	});

	it('never embeds the media\'s bytes -- only its stable path -- for a resolved form_ref', () => {
		const doc = baseValue();
		doc.chain.segments[1] = { ...doc.chain.segments[1], keyframe: { form_ref: { field: 'reference_image', path: 'old.png' } } };
		const caps = chainCaps();
		const identity = directorShotInputIdentity(doc, 's2', {
			caps,
			formData: { reference_image: { path: 'old.png', data: 'data:image/png;base64,AAAAAAAAAAAAAAAA' } }
		});
		expect(identity).not.toContain('base64');
		expect(identity).not.toContain('AAAAAAAAAAAAAAAA');
	});

	// ─── 'whole' reference pool ────────────────────────────────────────────────

	it('changes when a "whole" reference pool field gains an item, even though no shot object itself changed', () => {
		const doc = baseValue();
		const caps = chainCaps({ references: 'whole', referenceFields: ['references'] });
		const before = directorShotInputIdentity(doc, 's2', { caps, formData: { references: [{ path: '/r1.png' }] } });
		const after = directorShotInputIdentity(doc, 's2', {
			caps,
			formData: { references: [{ path: '/r1.png' }, { path: '/r2.png' }] }
		});
		expect(after).not.toBe(before);
	});

	it('a "whole" pool on a field this mode does NOT declare as a reference field is ignored', () => {
		const doc = baseValue();
		const caps = chainCaps({ references: 'whole', referenceFields: ['references'] });
		const before = directorShotInputIdentity(doc, 's2', { caps, formData: { unrelated_field: [{ path: '/r1.png' }] } });
		const after = directorShotInputIdentity(doc, 's2', {
			caps,
			formData: { unrelated_field: [{ path: '/r1.png' }, { path: '/r2.png' }] }
		});
		expect(after).toBe(before);
	});
});

// ─── directorPredecessorShotId ──────────────────────────────────────────────

describe('directorPredecessorShotId', () => {
	it('the first chain segment has no predecessor', () => {
		expect(directorPredecessorShotId(baseValue(), chainCaps(), 's1')).toBeNull();
	});

	it('a plain continuing chain segment depends on the one before it', () => {
		expect(directorPredecessorShotId(baseValue(), chainCaps(), 's2')).toBe('s1');
	});

	it('a segment forced to a fresh t2v cut has no predecessor', () => {
		const doc = baseValue();
		doc.chain.segments[1] = { ...doc.chain.segments[1], sub_type_override: 't2v' };
		expect(directorPredecessorShotId(doc, chainCaps(), 's2')).toBeNull();
	});

	it('continuationDisabled forces every segment independent', () => {
		const caps = chainCaps({ modes: { director: modeCap({ continuationDisabled: true }) } });
		expect(directorPredecessorShotId(baseValue(), caps, 's2')).toBeNull();
	});

	it('a timeline shot depends on its predecessor only when continue_from_previous is set', () => {
		const doc = baseValue({
			timeline: { fps: 24, shots: [timelineShot('t1'), timelineShot('t2', { continue_from_previous: true })] }
		});
		expect(directorPredecessorShotId(doc, timelineCaps(), 't2')).toBe('t1');
		expect(directorPredecessorShotId(doc, timelineCaps(), 't1')).toBeNull();
	});

	it('a timeline shot with continue_from_previous false has no predecessor', () => {
		const doc = baseValue({
			timeline: { fps: 24, shots: [timelineShot('t1'), timelineShot('t2', { continue_from_previous: false })] }
		});
		expect(directorPredecessorShotId(doc, timelineCaps(), 't2')).toBeNull();
	});
});
