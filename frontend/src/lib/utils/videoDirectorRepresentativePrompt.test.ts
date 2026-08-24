// ROOT CAUSE (three independent layers):
//
// A brand-new Director tab starts with exactly ONE chain segment, no
// keyframe (`createDefaultDirectorValue`) -- `deriveDirectorMode` derives
// that as `t2v`, not `director` (`singleShotEdges`: a single edgeless shot
// has neither a leading nor trailing edge). `buildDirectorSubmission` still
// correctly puts the shot's own typed text on the wire doc for that case
// (`extractSingleShot` + `joinPrompt`) -- proven by
// stageShotPromptPersistence.test.ts and videoDirectorEditorRoundTrip.test.ts.
//
// But MiniMax-H3's own pipeline (presets/native/MiniMax-H3/modes/refs/
// pipeline.yml) gates its director-aware prompt encoder AND the generator's
// `document` input on the wire doc's `mode == "director"` literally (lines
// 196, 294, 312) -- matching `build_director_plan()`'s own
// `document.get("mode") != "director"` early-return in
// src/pipelines/pipes/generator/video_minimax_h3/windows.py. A derived
// `t2v`/`i2v`/`flf` document (the DEFAULT single-shot state) falls through to
// the plain single-window path, whose prompt encoder reads
// `generation.prompts.first.positive` -- `request.prompts[0].positive`,
// built by `generate/+page.svelte`'s `startGeneration()` from
// `representativeDirectorPrompt(doc, caps)` -- NEVER the wire document's own
// `chain.segments[0].prompt`.
//
// `representativeDirectorPrompt`'s t2v/i2v/flf branch used to return only
// `value.global_prompt` ("Direction"), silently dropping the shot's own
// typed text. That is the actual break: the wire document was never missing
// the prompt (previous investigation proved that chain sound); the field
// that becomes `request.prompts[0].positive` was -- and for a preset whose
// pipeline routes solely off that field once `mode` isn't literally
// `"director"`, that field is the one that mattered. This explains both
// reported symptoms: (1) Generate looking dead until "Direction" carried
// text (the `hasValidPrompt` gate at generate/+page.svelte:1072 reads this
// same field) and (2) once Direction was filled, generations using it and
// ignoring the shot's own typed "description" entirely (the single-window
// path never saw it).
//
// Fixed by making the t2v/i2v/flf branch join the shot's own prompt the same
// way `buildDirectorSubmission`'s own single-shot branches already do
// (`extractSingleShot` + `joinPrompt`).
import { describe, it, expect } from 'vitest';
import {
	resolveDirectorCapabilities,
	normalizeDirectorValue,
	buildDirectorSubmission,
	dereferenceFormMediaRefs,
	representativeDirectorPrompt
} from './videoDirector';
import type { VideoDirectorValue, ChainSegment, DirectorCapabilities } from '$lib/types/videoDirector';

// Verbatim copy of presets/native/MiniMax-H3/preset.yml's `video_director:` block.
const H3_PRESET_RAW = {
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
				director: { keyframes: null, audio: false, continuation: null, max_overlap_frames: null }
			}
		}
	}
};

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
		chain: { fps: 24, segments: [], continuation: { overlap_frames: 0, stitch: true }, keyframes: [], audio: [] }
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

function singleShotDoc(prompt: string): VideoDirectorValue {
	const doc = baseDoc();
	doc.chain = {
		fps: 24,
		segments: [chainSegment('chain-1', prompt, 5)],
		continuation: { overlap_frames: 0, stitch: true },
		keyframes: [],
		audio: []
	};
	return doc;
}

describe('representativeDirectorPrompt: the default single-shot document (derives to t2v)', () => {
	it('the default document has exactly one edgeless shot, deriving to a non-"director" mode -- this is the scenario the pipeline gate excludes', () => {
		const caps = resolveDirectorCapabilities(H3_PRESET_RAW, 'refs')!;
		const doc = normalizeDirectorValue(singleShotDoc('a lighthouse at dusk'), caps);
		const wireDoc = buildDirectorSubmission(doc, caps);
		// This is exactly the field presets/native/MiniMax-H3/modes/refs/pipeline.yml
		// (lines 196, 294, 312) and windows.py's build_director_plan() gate on.
		expect(wireDoc.mode).not.toBe('director');
	});

	it('includes the shot\'s own typed prompt, not just Direction (global_prompt)', () => {
		const caps = resolveDirectorCapabilities(H3_PRESET_RAW, 'refs')!;
		const doc = normalizeDirectorValue(singleShotDoc('a lighthouse at dusk'), caps);
		expect(doc.global_prompt.trim()).toBe(''); // Direction left empty, as in the report
		expect(representativeDirectorPrompt(doc, caps)).toContain('a lighthouse at dusk');
	});

	it('joins Direction and the shot prompt when both are set, Direction first', () => {
		const caps = resolveDirectorCapabilities(H3_PRESET_RAW, 'refs')!;
		const doc = normalizeDirectorValue(singleShotDoc('a lighthouse at dusk'), caps);
		doc.global_prompt = 'cinematic, 35mm';
		const positive = representativeDirectorPrompt(doc, caps);
		expect(positive.indexOf('cinematic')).toBeLessThan(positive.indexOf('lighthouse'));
	});

	it('stays empty when neither Direction nor the shot carry text (unchanged behavior)', () => {
		const caps = resolveDirectorCapabilities(H3_PRESET_RAW, 'refs')!;
		const doc = normalizeDirectorValue(singleShotDoc(''), caps);
		expect(representativeDirectorPrompt(doc, caps)).toBe('');
	});

	it('video mode (no references override): same fix applies', () => {
		const caps = resolveDirectorCapabilities(H3_PRESET_RAW, 'video')!;
		const doc = normalizeDirectorValue(singleShotDoc('a lighthouse at dusk'), caps);
		expect(representativeDirectorPrompt(doc, caps)).toContain('a lighthouse at dusk');
	});
});

describe('representativeDirectorPrompt: multi-shot director mode is untouched (regression guard)', () => {
	function multiShotDoc(caps: DirectorCapabilities): VideoDirectorValue {
		const doc = baseDoc();
		doc.chain = {
			fps: 24,
			segments: [chainSegment('chain-1', 'a lighthouse at dusk', 5), chainSegment('chain-2', 'a boat leaving harbour', 5)],
			continuation: { overlap_frames: 0, stitch: true },
			keyframes: [],
			audio: []
		};
		return normalizeDirectorValue(doc, caps);
	}

	it('refs mode: joins every segment prompt, same as before this fix', () => {
		const caps = resolveDirectorCapabilities(H3_PRESET_RAW, 'refs')!;
		const doc = multiShotDoc(caps);
		expect(buildDirectorSubmission(doc, caps).mode).toBe('director');
		const positive = representativeDirectorPrompt(doc, caps);
		expect(positive).toContain('a lighthouse at dusk');
		expect(positive).toContain('a boat leaving harbour');
	});
});

describe('generate/+page.svelte startGeneration attach block, replayed verbatim against the real H3 preset.yml capabilities', () => {
	it('refs mode, single shot: both the wire document AND request.prompts[0].positive carry the typed shot prompt', () => {
		const caps = resolveDirectorCapabilities(H3_PRESET_RAW, 'refs')!;
		const currentTabFormData = { references: [{ path: '/pool/a.png' }] };

		// Verbatim from generate/+page.svelte:958-977
		const doc = normalizeDirectorValue(singleShotDoc('a lighthouse at dusk'), caps);
		const wireDoc = buildDirectorSubmission(doc, caps);
		const { doc: resolvedWireDoc, errors: formRefErrors } = dereferenceFormMediaRefs(wireDoc, currentTabFormData);
		expect(formRefErrors).toEqual([]);

		const formDataForRequest: Record<string, unknown> = {
			...currentTabFormData,
			video_director: resolvedWireDoc
		};
		const promptsArray = [
			{ positive: representativeDirectorPrompt(doc, caps), negative: doc.negative_prompt || '' }
		];

		const wire = formDataForRequest.video_director as typeof resolvedWireDoc;
		expect(wire.segments[0].prompt).toContain('a lighthouse at dusk');
		// Before the fix this was '' -- the field the pipeline actually reads
		// for a derived (non-"director") document, per the header comment above.
		expect(promptsArray[0].positive).toContain('a lighthouse at dusk');
	});

	it('video mode: same replay, for comparison', () => {
		const caps = resolveDirectorCapabilities(H3_PRESET_RAW, 'video')!;
		const doc = normalizeDirectorValue(singleShotDoc('a lighthouse at dusk'), caps);
		const wireDoc = buildDirectorSubmission(doc, caps);
		const { doc: resolvedWireDoc } = dereferenceFormMediaRefs(wireDoc, null);

		expect(resolvedWireDoc.segments[0].prompt).toContain('a lighthouse at dusk');
		expect(representativeDirectorPrompt(doc, caps)).toContain('a lighthouse at dusk');
	});
});
