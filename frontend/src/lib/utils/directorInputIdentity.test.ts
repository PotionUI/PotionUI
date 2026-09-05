import { describe, it, expect } from 'vitest';
import {
	directorShotInputIdentity,
	directorPredecessorShotId,
	directorPredecessorOutputKey,
	hasVersionedShotIdentity,
	isUnverifiedShotIdentity,
	NATIVE_CONTINUATION_OUTPUT_KEY
} from './directorInputIdentity';
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

	it('a formData field change on ANY field is honored, not just declared reference fields -- the whole form reaches the request', () => {
		// Superseded rule: this used to be filtered to `caps.referenceFields`.
		// `request.form_data` is `{...tab.formData, video_director: ...}`
		// (routes/generate/+page.svelte) -- EVERY key reaches the request, under
		// whatever name this preset's own form uses, so narrowing to a
		// caps-declared allowlist missed real inputs (steps/cfg/seed/model/...).
		const doc = baseValue();
		const caps = chainCaps({ references: 'whole', referenceFields: ['references'] });
		const before = directorShotInputIdentity(doc, 's2', { caps, formData: { unrelated_field: [{ path: '/r1.png' }] } });
		const after = directorShotInputIdentity(doc, 's2', {
			caps,
			formData: { unrelated_field: [{ path: '/r1.png' }, { path: '/r2.png' }] }
		});
		expect(after).not.toBe(before);
	});

	// ─── Effective generation form settings (steps/cfg/seed/model/...) ────────

	it('changes when a generation form field the request actually sends changes (steps, cfg, seed, model -- whatever this preset names them)', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const before = directorShotInputIdentity(doc, 's2', { caps, formData: { steps: 20, cfg: 7, seed: 42, model: 'sdxl-base' } });
		const after = directorShotInputIdentity(doc, 's2', { caps, formData: { steps: 30, cfg: 7, seed: 42, model: 'sdxl-base' } });
		expect(after).not.toBe(before);
	});

	it('the stale video_director key already present in formData is excluded (the live document is hashed separately, not re-hashed from a stale form snapshot)', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const withoutKey = directorShotInputIdentity(doc, 's2', { caps, formData: { steps: 20 } });
		const withStaleDirectorKey = directorShotInputIdentity(doc, 's2', {
			caps,
			formData: { steps: 20, video_director: { anything: 'a stale, unrelated snapshot' } }
		});
		expect(withStaleDirectorKey).toBe(withoutKey);
	});

	// ─── Chain continuation geometry ───────────────────────────────────────────

	it('changes when chain.continuation.overlap_frames changes, for every shot in the chain', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const beforeS2 = directorShotInputIdentity(doc, 's2', { caps, formData: null });
		const edited = baseValue();
		edited.chain.continuation = { overlap_frames: 24, stitch: true };
		expect(directorShotInputIdentity(edited, 's2', { caps, formData: null })).not.toBe(beforeS2);
	});

	it('chain continuation geometry never leaks into a TIMELINE shot\'s identity', () => {
		const doc = baseValue();
		const caps = timelineCaps();
		const before = directorShotInputIdentity(doc, 't1', { caps, formData: null });
		const edited = baseValue();
		edited.chain.continuation = { overlap_frames: 999, stitch: false };
		expect(directorShotInputIdentity(edited, 't1', { caps, formData: null })).toBe(before);
	});

	// ─── Bounded regardless of payload size ────────────────────────────────────

	it('a directly embedded (non-form_ref) media payload never blows up the identity size or appears verbatim', () => {
		const doc = baseValue();
		const hugePayload = 'A'.repeat(50000);
		doc.chain.segments[1] = { ...doc.chain.segments[1], keyframe: { path: hugePayload, type: 'image' } as any };
		const caps = chainCaps();
		const identity = directorShotInputIdentity(doc, 's2', { caps, formData: null });
		expect(identity).not.toBeNull();
		expect(identity!.length).toBeLessThan(2200);
		expect(identity).not.toContain(hugePayload);
	});

	it('stays bounded even when the total payload is large from MANY small fields, not one huge one', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const bigFormData: Record<string, unknown> = {};
		for (let i = 0; i < 500; i++) bigFormData[`field_${i}`] = `value-${i}`;
		const identity = directorShotInputIdentity(doc, 's2', { caps, formData: bigFormData });
		expect(identity).not.toBeNull();
		expect(identity!.length).toBeLessThan(2200);
	});

	// ─── Media revision identity: same path, different content ────────────────

	it('a form_ref resolving to the SAME path but different revision evidence (a replaced file) IS a change', () => {
		const doc = baseValue();
		doc.chain.segments[1] = { ...doc.chain.segments[1], keyframe: { form_ref: { field: 'reference_image', path: 'same.png' } } };
		const caps = chainCaps();
		const before = directorShotInputIdentity(doc, 's2', {
			caps,
			formData: { reference_image: { path: 'same.png', metadata: { size: 1000, width: 512, height: 512 } } }
		});
		const after = directorShotInputIdentity(doc, 's2', {
			caps,
			formData: { reference_image: { path: 'same.png', metadata: { size: 2048, width: 768, height: 768 } } }
		});
		expect(before).not.toBeNull();
		expect(after).not.toBe(before);
	});

	it('a form_ref at the same path with the SAME revision evidence is unchanged', () => {
		const doc = baseValue();
		doc.chain.segments[1] = { ...doc.chain.segments[1], keyframe: { form_ref: { field: 'reference_image', path: 'same.png' } } };
		const caps = chainCaps();
		const revisioned = { path: 'same.png', metadata: { size: 1000, width: 512, height: 512 } };
		const a = directorShotInputIdentity(doc, 's2', { caps, formData: { reference_image: revisioned } });
		const b = directorShotInputIdentity(doc, 's2', { caps, formData: { reference_image: { ...revisioned } } });
		expect(a).toBe(b);
	});

	it('a form_ref with NO revision evidence at all is flagged unverified, not silently asserted fresh', () => {
		const doc = baseValue();
		doc.chain.segments[1] = { ...doc.chain.segments[1], keyframe: { form_ref: { field: 'reference_image', path: 'same.png' } } };
		const caps = chainCaps();
		const identity = directorShotInputIdentity(doc, 's2', { caps, formData: { reference_image: { path: 'same.png' } } });
		expect(identity).not.toBeNull();
		expect(hasVersionedShotIdentity(identity)).toBe(true);
		expect(isUnverifiedShotIdentity(identity)).toBe(true);
	});

	it('a form_ref WITH revision evidence (size) is verified, not flagged unverified', () => {
		const doc = baseValue();
		doc.chain.segments[1] = { ...doc.chain.segments[1], keyframe: { form_ref: { field: 'reference_image', path: 'same.png' } } };
		const caps = chainCaps();
		const identity = directorShotInputIdentity(doc, 's2', {
			caps,
			formData: { reference_image: { path: 'same.png', metadata: { size: 1000 } } }
		});
		expect(isUnverifiedShotIdentity(identity)).toBe(false);
	});

	it('shape-only metadata (width/height/duration/fps) is NOT revision evidence -- a same-shaped replacement stays unverified', () => {
		const doc = baseValue();
		doc.chain.segments[1] = { ...doc.chain.segments[1], keyframe: { form_ref: { field: 'reference_image', path: 'same.png' } } };
		const caps = chainCaps();
		// Two DIFFERENT photos (or a re-export of the same one) routinely share
		// identical dimensions/duration/fps -- shape alone must never read as
		// "same file".
		const before = directorShotInputIdentity(doc, 's2', {
			caps,
			formData: { reference_image: { path: 'same.png', metadata: { width: 64, height: 64, duration_seconds: 3, fps: 24 } } }
		});
		const after = directorShotInputIdentity(doc, 's2', {
			caps,
			formData: { reference_image: { path: 'same.png', metadata: { width: 64, height: 64, duration_seconds: 3, fps: 24 } } }
		});
		expect(isUnverifiedShotIdentity(before)).toBe(true);
		expect(isUnverifiedShotIdentity(after)).toBe(true);
	});

	it('a size/hash/etc IS real revision evidence, even alongside shape-only fields -- verified, not unverified', () => {
		const doc = baseValue();
		doc.chain.segments[1] = { ...doc.chain.segments[1], keyframe: { form_ref: { field: 'reference_image', path: 'same.png' } } };
		const caps = chainCaps();
		const identity = directorShotInputIdentity(doc, 's2', {
			caps,
			formData: { reference_image: { path: 'same.png', metadata: { width: 64, height: 64, size: 4096 } } }
		});
		expect(isUnverifiedShotIdentity(identity)).toBe(false);
	});

	it('an inline data: payload alongside a stable path is real revision evidence (digested, not the pointer\'s bare path alone)', () => {
		const doc = baseValue();
		doc.chain.segments[1] = { ...doc.chain.segments[1], keyframe: { form_ref: { field: 'reference_image', path: 'same.png' } } };
		const caps = chainCaps();
		const before = directorShotInputIdentity(doc, 's2', {
			caps,
			formData: { reference_image: { path: 'same.png', data: 'data:image/png;base64,AAAA' } }
		});
		const after = directorShotInputIdentity(doc, 's2', {
			caps,
			formData: { reference_image: { path: 'same.png', data: 'data:image/png;base64,BBBB' } }
		});
		expect(isUnverifiedShotIdentity(before)).toBe(false);
		expect(after).not.toBe(before);
		expect(before).not.toContain('AAAA');
		expect(after).not.toContain('BBBB');
	});

	// ─── Generation context: preset/variant/mode are required scope ───────────

	it('changes when the preset id changes, even though the document and formData did not', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const before = directorShotInputIdentity(doc, 's2', {
			caps,
			formData: null,
			generationContext: { presetId: 'preset-a', variant: 'default', mode: 'video' }
		});
		const after = directorShotInputIdentity(doc, 's2', {
			caps,
			formData: null,
			generationContext: { presetId: 'preset-b', variant: 'default', mode: 'video' }
		});
		expect(after).not.toBe(before);
	});

	it('changes when the variant changes', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const ctx = (variant: string) => ({
			caps,
			formData: null,
			generationContext: { presetId: 'preset-a', variant, mode: 'video' }
		});
		expect(directorShotInputIdentity(doc, 's2', ctx('a'))).not.toBe(directorShotInputIdentity(doc, 's2', ctx('b')));
	});

	it('changes when the mode changes', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const ctx = (mode: string) => ({
			caps,
			formData: null,
			generationContext: { presetId: 'preset-a', variant: 'default', mode }
		});
		expect(directorShotInputIdentity(doc, 's2', ctx('video'))).not.toBe(directorShotInputIdentity(doc, 's2', ctx('refs')));
	});

	it('a caller with no generationContext yet gets the neutral EMPTY_GENERATION_CONTEXT, distinct from any real one', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const omitted = directorShotInputIdentity(doc, 's2', { caps, formData: null });
		const explicitNull = directorShotInputIdentity(doc, 's2', { caps, formData: null, generationContext: null });
		const real = directorShotInputIdentity(doc, 's2', {
			caps,
			formData: null,
			generationContext: { presetId: 'preset-a', variant: null, mode: null }
		});
		expect(omitted).toBe(explicitNull); // both fall back to EMPTY_GENERATION_CONTEXT
		expect(real).not.toBe(omitted);
	});

	// ─── Canonical (sorted) object keys; array order stays significant ────────

	it('object key order never changes the identity, at any nesting level', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const a = directorShotInputIdentity(doc, 's2', { caps, formData: { steps: 20, cfg: 7 } });
		const b = directorShotInputIdentity(doc, 's2', { caps, formData: { cfg: 7, steps: 20 } });
		expect(a).toBe(b);
	});

	it('nested object key order never changes the identity either', () => {
		const doc = baseValue();
		const caps = chainCaps();
		const a = directorShotInputIdentity(doc, 's2', { caps, formData: { model: { name: 'sdxl', path: '/m.safetensors' } } });
		const b = directorShotInputIdentity(doc, 's2', { caps, formData: { model: { path: '/m.safetensors', name: 'sdxl' } } });
		expect(a).toBe(b);
	});

	it('array order DOES change the identity -- shots/keyframes/segments are ordered sequences, not sets', () => {
		const caps = chainCaps();
		const forward = baseValue();
		forward.chain.keyframes = [
			{ id: 'kf-1', at: 1, strength: 1, media: { path: '/a.png' } },
			{ id: 'kf-2', at: 2, strength: 1, media: { path: '/b.png' } }
		];
		const reversed = baseValue();
		reversed.chain.keyframes = [...forward.chain.keyframes].reverse();
		const a = directorShotInputIdentity(forward, 's2', { caps, formData: null });
		const b = directorShotInputIdentity(reversed, 's2', { caps, formData: null });
		expect(a).not.toBe(b);
	});
});

describe('directorPredecessorOutputKey', () => {
	it('chain routing has no discrete consumed output -- always the native-continuation sentinel, never the shot id', () => {
		expect(directorPredecessorOutputKey(chainCaps(), 's1')).toBe(NATIVE_CONTINUATION_OUTPUT_KEY);
	});

	it('timeline routing uses the predecessor\'s own shot id (matches directorContinuation.ts\'s resolvePredecessorFrame)', () => {
		expect(directorPredecessorOutputKey(timelineCaps(), 't1')).toBe('t1');
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
