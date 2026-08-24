// @vitest-environment jsdom
//
// A typed shot prompt has to survive a whole chain: InlineChipEditor's DOM
// input -> PromptSegment's `change` -> SegmentedPromptEditor's `segmentsChange`
// -> StageShot's `withShotPromptSegments` -> Stage's bindable `doc` -> back out
// through `onDoc`. 9eaa22c7 rewrote SegmentedPromptEditor's prop/dispatch
// surface (flow mode removed); this mounts the real StageShot and drives a
// real DOM edit to prove the chain still reaches `onDoc` with the typed text
// in both `prompt` and `prompt_segments`, then that a normalize/validate/
// submit round trip on that emitted document keeps the text -- for both a
// references (H3-style) chain and a plain segment-routed chain.
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		searchPhrasebook: vi.fn().mockResolvedValue({ success: true, data: [] }),
		toggleValueActive: vi.fn().mockResolvedValue({ success: true }),
		getFileURL: (id: string) => id,
		listGenerationMedia: vi.fn().mockResolvedValue({ success: true, data: [] }),
		getUploadInfo: vi.fn().mockResolvedValue({ success: true, data: null }),
		getBaseURL: () => 'http://localhost',
		getToken: () => null,
		setOnAuthExpired: vi.fn(),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getClient: () => ({ get: vi.fn(), post: vi.fn() })
	}
}));

const { mount, unmount, flushSync } = await import('svelte');
const { default: StageShot } = await import(
	'$lib/components/video-director/stage-rail/StageShot.svelte'
);
const { deriveStageModel } = await import('$lib/components/video-director/stage-rail/stageModel');
const {
	resolveDirectorCapabilities,
	normalizeDirectorValue,
	validateDirector,
	buildDirectorSubmission
} = await import('$lib/utils/videoDirector');

import type { VideoDirectorValue, ChainSegment, DirectorCapabilities } from '$lib/types/videoDirector';
import type { StageShotModel } from '$lib/components/video-director/stage-rail/stageModel';

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

function blankChainSegment(id: string, duration: number, overrides: Partial<ChainSegment> = {}): ChainSegment {
	return {
		id,
		prompt: '',
		prompt_segments: [],
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

function wanCaps(): DirectorCapabilities {
	return {
		presetModes: null,
		modes: {
			director: {
				tips: [],
				maxDuration: null,
				audio: false,
				icLora: false,
				maxKeyframes: null,
				perSegmentLoras: true,
				keyframes: 'first_only',
				maxSegments: 8,
				maxFramesPerSegment: 81,
				defaultSegmentDuration: 5,
				continuation: null,
				maxOverlapFrames: 81,
				continuationDisabled: false
			}
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

async function settle() {
	for (let i = 0; i < 5; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

let cleanup: (() => void) | undefined;

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

async function typeIntoFirstSegment(target: HTMLElement, text: string) {
	const editable = target.querySelector('[contenteditable="true"]') as HTMLElement | null;
	if (!editable) throw new Error('no contenteditable segment editor found in StageShot render');
	editable.textContent = text;
	editable.dispatchEvent(new Event('input', { bubbles: true }));
	await settle();
}

async function driveShotEdit(
	doc: VideoDirectorValue,
	caps: DirectorCapabilities,
	formData: Record<string, unknown> | null,
	text: string
): Promise<VideoDirectorValue | null> {
	const model = deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-1' }, formData).selected as StageShotModel;

	const target = document.createElement('div');
	document.body.appendChild(target);

	let emitted: VideoDirectorValue | null = null;
	const instance = mount(StageShot, {
		target,
		props: {
			model,
			doc,
			caps,
			formData,
			presetId: 'test-preset',
			onDoc: (next: VideoDirectorValue) => {
				emitted = next;
			}
		}
	});
	cleanup = () => unmount(instance);

	await settle();
	await typeIntoFirstSegment(target, text);

	return emitted;
}

describe('StageShot -> onDoc: a typed shot prompt reaches the document', () => {
	it('H3 refs profile: lands in chain.segments[0].prompt and prompt_segments, and survives normalize/validate/submit', async () => {
		const caps = resolveDirectorCapabilities(H3_REFS_PRESET_RAW, 'refs')!;
		const doc = baseDoc();
		doc.chain = {
			fps: 24,
			segments: [
				blankChainSegment('chain-1', 5),
				blankChainSegment('chain-2', 5, {
					prompt: 'a boat leaving harbour',
					prompt_segments: [{ id: 'chain-2-p0', content: 'a boat leaving harbour', chips: {}, type: 'content', enabled: true }]
				})
			],
			continuation: { overlap_frames: 0, stitch: true },
			keyframes: [],
			audio: []
		};
		const formData = { references: [{ path: '/pool/a.png' }] };

		const emitted = await driveShotEdit(doc, caps, formData, 'a lighthouse at dusk');

		expect(emitted).not.toBeNull();
		const seg = emitted!.chain.segments.find((s) => s.id === 'chain-1')!;
		expect(seg.prompt).toContain('a lighthouse at dusk');
		expect(seg.prompt_segments.map((s) => s.content).join(' ')).toContain('a lighthouse at dusk');

		const normalized = normalizeDirectorValue(emitted, caps);
		const validation = validateDirector(normalized, caps);
		expect(validation.ok).toBe(true);

		const submission = buildDirectorSubmission(normalized, caps);
		expect(submission.segments[0].prompt).toContain('a lighthouse at dusk');
	});

	it('video (non-refs, Wan-style) profile: same chain', async () => {
		const caps = wanCaps();
		const doc = baseDoc();
		doc.chain = {
			fps: 16,
			segments: [
				blankChainSegment('chain-1', 5),
				blankChainSegment('chain-2', 5, {
					prompt: 'a boat leaving harbour',
					prompt_segments: [{ id: 'chain-2-p0', content: 'a boat leaving harbour', chips: {}, type: 'content', enabled: true }]
				})
			],
			continuation: { overlap_frames: 16, stitch: true },
			keyframes: [],
			audio: []
		};

		const emitted = await driveShotEdit(doc, caps, null, 'a lighthouse at dusk');

		expect(emitted).not.toBeNull();
		const seg = emitted!.chain.segments.find((s) => s.id === 'chain-1')!;
		expect(seg.prompt).toContain('a lighthouse at dusk');
		expect(seg.prompt_segments.map((s) => s.content).join(' ')).toContain('a lighthouse at dusk');

		const normalized = normalizeDirectorValue(emitted, caps);
		const validation = validateDirector(normalized, caps);
		expect(validation.ok).toBe(true);

		const submission = buildDirectorSubmission(normalized, caps);
		expect(submission.segments[0].prompt).toContain('a lighthouse at dusk');
	});
});

describe('StageShot -> onDoc: single-shot chain (derives to t2v, not director)', () => {
	it('H3 refs profile, one shot: typed prompt lands and t2v validation passes without touching Direction', async () => {
		const caps = resolveDirectorCapabilities(H3_REFS_PRESET_RAW, 'refs')!;
		const doc = baseDoc();
		doc.chain = {
			fps: 24,
			segments: [blankChainSegment('chain-1', 5)],
			continuation: { overlap_frames: 0, stitch: true },
			keyframes: [],
			audio: []
		};
		const formData = { references: [{ path: '/pool/a.png' }] };

		const emitted = await driveShotEdit(doc, caps, formData, 'a lighthouse at dusk');

		expect(emitted).not.toBeNull();
		const seg = emitted!.chain.segments.find((s) => s.id === 'chain-1')!;
		console.log('SINGLE-SHOT emitted seg.prompt =', JSON.stringify(seg.prompt), 'prompt_segments =', JSON.stringify(seg.prompt_segments));

		const normalized = normalizeDirectorValue(emitted, caps);
		console.log('derived mode after normalize+modeless =', JSON.stringify(normalized.mode));

		const validation = validateDirector(normalized, caps);
		console.log('validation =', JSON.stringify(validation));
		expect(seg.prompt).toContain('a lighthouse at dusk');
		expect(validation.ok).toBe(true);
	});
});
