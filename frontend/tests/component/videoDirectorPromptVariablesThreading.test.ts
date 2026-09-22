// @vitest-environment jsdom
//
// PromptSection.svelte threads `variables`/`variableRolls`/`onVariableDefChange`
// and reuses `onOpenVariables` as `onOpenVariableManager` into every plain-prompt
// SegmentedPromptEditor it mounts, but VideoDirectorEditor only ever received
// `onOpenVariables` + `variableCount` (for its own header button) -- the chain
// down through ShotConsole -> ShotStage -> StageBeat never carried the four
// variable props on to the shot's own SegmentedPromptEditor, so the `$`
// variable dropdown was empty and every `${name}` usage rendered undefined
// inside Video Director mode. This mounts the real VideoDirectorEditor,
// selects a shot's prompt beat, and proves the props reach that beat's
// SegmentedPromptEditor toolbar (same pattern as videoDirectorEditorRoundTrip.test.ts).
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

const { flushSync } = await import('svelte');
const { createClassComponent } = await import('svelte/legacy');
const { default: VideoDirectorEditor } = await import(
	'$lib/components/video-director/VideoDirectorEditor.svelte'
);
const { resolveDirectorCapabilities } = await import('$lib/utils/videoDirector');

import type { VideoDirectorValue, ChainSegment, DirectorCapabilities } from '$lib/types/videoDirector';
import type { VariablesMap } from '$lib/utils/variableDefs';

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

describe('VideoDirectorEditor: prompt-variable state reaches the shot prompt editor', () => {
	it('threads variables and onOpenVariableManager down to the selected beat SegmentedPromptEditor', async () => {
		const caps = h3RefsCaps();
		const initial = baseDoc();
		initial.chain = {
			fps: 24,
			segments: [blankChainSegment('chain-1', 5)],
			continuation: { overlap_frames: 0, stitch: true },
			keyframes: [],
			audio: []
		};
		const formData = { references: [{ path: '/pool/a.png' }] };
		const variables: VariablesMap = { mood: { type: 'text', value: 'melancholy' } };
		const onOpenVariables = vi.fn();

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
				variables,
				variableCount: 1,
				onChange: vi.fn(),
				onOpenVariables
			}
		});
		cleanup = () => instance.$destroy();

		await settle();

		const beat = target.querySelector('.beat-b') as HTMLElement | null;
		expect(beat).toBeTruthy();
		beat!.click();
		await settle();

		const beatVariablesButton = target.querySelector('button[aria-label^="Variables"]') as HTMLButtonElement | null;
		expect(beatVariablesButton).not.toBeNull();
		expect(beatVariablesButton!.getAttribute('aria-label')).toContain('1');

		beatVariablesButton!.click();
		expect(onOpenVariables).toHaveBeenCalledTimes(1);
	});
});
