// @vitest-environment jsdom
//
// The rail's "Refs" lane stacks small avatar swatches for the whole-film
// reference pool, drawing every pool item's thumbnail with a bare
// `<img src={opt.item.url}>` and no gate on `opt.item.type` - same defect as
// StageShot's per-shot picker (stageShotReferencePreview.test.ts), different
// component: an audio reference has a `url` but nothing an <img> can decode.
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getFileURL: (id: string) => id,
		getBaseURL: () => 'http://localhost',
		getToken: () => null,
		setOnAuthExpired: vi.fn(),
		getClient: () => ({ get: vi.fn(), post: vi.fn() })
	}
}));

const { mount, unmount, flushSync } = await import('svelte');
const { default: Rail } = await import('$lib/components/video-director/stage-rail/Rail.svelte');

import type { VideoDirectorValue, DirectorCapabilities } from '$lib/types/videoDirector';

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

function wholeFilmRefCaps(): DirectorCapabilities {
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
		references: 'whole',
		referenceFields: ['references', 'reference_audios']
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

describe('Rail Refs lane: whole-film reference pool preview', () => {
	it('renders an audio reference through the Icon fallback, never an <img>', async () => {
		const caps = wholeFilmRefCaps();
		const doc = baseDoc();
		const formData = {
			references: [{ path: '/pool/a.png', type: 'image', url: '/pool/a.png' }],
			reference_audios: [{ path: '/pool/voice.mp3', type: 'audio', url: '/pool/voice.mp3' }]
		};

		const target = document.createElement('div');
		document.body.appendChild(target);

		const instance = mount(Rail, {
			target,
			props: { doc, caps, formData }
		});
		cleanup = () => unmount(instance);
		await settle();

		expect(target.textContent).toContain('Refs');

		const imgs = Array.from(target.querySelectorAll('img'));
		expect(imgs.some((img) => img.getAttribute('src') === '/pool/a.png')).toBe(true);
		// The audio pool item must never reach an <img src="...mp3"> (a
		// broken-image icon in a real browser) - it renders through the Icon
		// fallback instead.
		expect(imgs.some((img) => img.getAttribute('src') === '/pool/voice.mp3')).toBe(false);
	});
});
