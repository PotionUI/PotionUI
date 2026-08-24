// @vitest-environment jsdom
//
// The per-shot reference picker (StageShot's "References for this shot" grid)
// drew every pool item's thumbnail with a bare `<img src={opt.item.url}>`,
// with no gate on `opt.item.type` - an audio reference has a `url` (the served
// file) but nothing an <img> can decode, so it rendered a broken-image icon.
// This mounts the real StageShot, opens the picker, and proves an audio pool
// item renders through the Icon fallback instead of ever reaching an <img>.
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
const { resolveDirectorCapabilities } = await import('$lib/utils/videoDirector');

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

// Same fixture `stageShotPromptPersistence.test.ts` uses for a `references:
// 'per_shot'` capability profile - the audio reference bug only reaches this
// picker on that profile (`whole` never renders per-item thumbnails at all).
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
			reference_fields: ['references', 'reference_audios'],
			modes: { director: { keyframes: null, audio: false, continuation: null, max_overlap_frames: null } }
		}
	}
};

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

describe('StageShot per-shot reference picker: non-image pool items', () => {
	it('renders an audio reference through the Icon fallback, never an <img>', async () => {
		const caps = resolveDirectorCapabilities(H3_REFS_PRESET_RAW, 'refs')! as DirectorCapabilities;
		const doc = baseDoc();
		doc.chain = {
			fps: 24,
			segments: [
				blankChainSegment('chain-1', 5),
				blankChainSegment('chain-2', 5, { prompt: 'a boat leaving harbour' })
			],
			continuation: { overlap_frames: 0, stitch: true },
			keyframes: [],
			audio: []
		};
		const formData = {
			references: [{ path: '/pool/a.png', type: 'image', url: '/pool/a.png' }],
			reference_audios: [{ path: '/pool/voice.mp3', type: 'audio', url: '/pool/voice.mp3' }]
		};

		const model = deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-1' }, formData).selected as StageShotModel;

		const target = document.createElement('div');
		document.body.appendChild(target);

		const instance = mount(StageShot, {
			target,
			props: {
				model,
				doc,
				caps,
				formData,
				presetId: 'test-preset',
				onDoc: () => {}
			}
		});
		cleanup = () => unmount(instance);
		await settle();

		const toggle = Array.from(target.querySelectorAll('button')).find((b) =>
			/^(All \(\d+\)|\d+ of \d+)$/.test(b.textContent?.trim() ?? '')
		);
		expect(toggle).toBeTruthy();
		toggle!.click();
		await settle();

		const labels = Array.from(target.querySelectorAll('label'));
		const imageRow = labels.find((l) => l.textContent?.includes('References'));
		const audioRow = labels.find((l) => l.textContent?.includes('Reference Audios'));

		expect(imageRow).toBeTruthy();
		expect(audioRow).toBeTruthy();

		// The image pool item gets a real thumbnail...
		expect(imageRow!.querySelector('img')).toBeTruthy();
		// ...the audio one must never reach an <img src="...mp3"> (a broken-image
		// icon in a real browser) - it renders through the Icon fallback instead.
		expect(audioRow!.querySelector('img')).toBeNull();
		expect(audioRow!.querySelector('svg')).toBeTruthy();
	});
});
