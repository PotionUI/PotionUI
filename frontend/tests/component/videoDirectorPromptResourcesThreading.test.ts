// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { PromptResourceSpec } from '$lib/utils/promptResources';

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

const { flushSync } = await import('svelte');
const { createClassComponent } = await import('svelte/legacy');
const { default: VideoDirectorEditor } = await import(
	'$lib/components/video-director/VideoDirectorEditor.svelte'
);
const { resolveDirectorCapabilities } = await import('$lib/utils/videoDirector');

import type { VideoDirectorValue, ChainSegment, DirectorCapabilities } from '$lib/types/videoDirector';

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
		steps: null,
		cfg: null,
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

function h3RefsCaps(): DirectorCapabilities {
	return resolveDirectorCapabilities(H3_REFS_PRESET_RAW, 'refs')!;
}

const H3_REFS_PROMPT_RESOURCES: PromptResourceSpec[] = [
	{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' },
	{ field: 'reference_videos', kind: 'video', label: 'Videos', token: '<Video @>' },
	{ field: 'reference_audios', kind: 'audio', label: 'Audio', token: '<Audio @>' }
];

async function settle() {
	for (let i = 0; i < 5; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

function typeAtSignInto(editorEl: HTMLElement) {
	editorEl.focus();
	const range = document.createRange();
	range.selectNodeContents(editorEl);
	range.collapse(false);
	const textNode = document.createTextNode('@');
	range.insertNode(textNode);
	range.setStart(textNode, textNode.length);
	range.collapse(true);
	const selection = window.getSelection();
	selection?.removeAllRanges();
	selection?.addRange(range);
	editorEl.dispatchEvent(new Event('input', { bubbles: true }));
}

let cleanup: (() => void) | undefined;

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('VideoDirectorEditor: prompt-resource `@` picker reaches the shot prompt editor', () => {
	it('lists the mode\'s mapped reference groups when @ is typed inside a shot beat', async () => {
		const caps = h3RefsCaps();
		const initial = baseDoc();
		initial.chain = {
			fps: 24,
			segments: [blankChainSegment('chain-1', 5)],
			continuation: { overlap_frames: 0, stitch: true },
			keyframes: [],
			audio: []
		};
		const formData = {
			references: [{ relative_path: 'a.png' }, { relative_path: 'b.png' }],
			reference_videos: [],
			reference_audios: [{ relative_path: 'c.mp3' }]
		};

		const target = document.createElement('div');
		document.body.appendChild(target);

		const instance = createClassComponent({
			component: VideoDirectorEditor as never,
			target,
			props: {
				value: initial,
				capabilities: caps,
				presetId: 'test-preset',
				formData,
				promptResources: H3_REFS_PROMPT_RESOURCES,
				onChange: vi.fn()
			}
		});
		cleanup = () => instance.$destroy();

		await settle();

		expect(target.querySelector('.beat-b')).toBeNull();
		await settle();

		const editorEl = target.querySelector('.stage-beat .inline-chip-editor') as HTMLElement | null;
		expect(editorEl).not.toBeNull();

		typeAtSignInto(editorEl!);
		flushSync();

		const picker = document.querySelector('.resource-picker');
		expect(picker).not.toBeNull();
		const text = picker!.textContent || '';
		expect(text).toContain('Pictures');
		expect(text).toContain('2 available');
		expect(text).toContain('Audio');
		expect(text).toContain('1 available');
	});
});
