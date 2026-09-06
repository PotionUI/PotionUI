// @vitest-environment jsdom
//
// Regression for the maintainer-reported "effect_update_depth_exceeded" at
// ShotConsole.svelte's onCheckedChange mirror: the generate page (legacy
// mode) writes the mirrored checked set into a per-tab map from the callback
// and reads it back in a `$:` statement; the callback reaches ShotConsole
// through two legacy passthrough components (GenerationPanels ->
// PromptSection). The fixtures under ./fixtures/DirectorCheckedLoop* mirror
// that exact chain around the real VideoDirectorEditor/ShotConsole.
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
const { default: Page } = await import('./fixtures/DirectorCheckedLoopPage.svelte');
const { resolveDirectorCapabilities } = await import('$lib/utils/videoDirector');

import type { VideoDirectorValue, ChainSegment } from '$lib/types/videoDirector';

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

function segment(id: string): ChainSegment {
	return {
		id,
		prompt: '',
		prompt_segments: [],
		duration: 5,
		loras: null,
		keyframe: null,
		keyframe_strength: 1,
		last_keyframe: null,
		last_keyframe_strength: 1,
		sub_type_override: null,
		steps: null,
		cfg: null
	};
}

function doc(): VideoDirectorValue {
	return {
		schema_version: 1,
		mode: 'director',
		global_prompt: '',
		global_prompt_segments: [],
		negative_prompt: '',
		negative_prompt_segments: [],
		simple: { duration: 5, fps: 24, start_image: null, first_frame: null, last_frame: null },
		timeline: { fps: 24, shots: [] },
		chain: {
			fps: 24,
			segments: [segment('chain-1'), segment('chain-2')],
			continuation: { overlap_frames: 0, stitch: true },
			keyframes: [],
			audio: []
		}
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

describe('ShotConsole checked-set mirror through the legacy page chain', () => {
	it('mounts without an effect loop and mirrors a row toggle up to the page exactly once per change', async () => {
		const caps = resolveDirectorCapabilities(H3_REFS_PRESET_RAW, 'refs')!;
		const target = document.createElement('div');
		document.body.appendChild(target);

		let instance: ReturnType<typeof createClassComponent> | undefined;
		expect(() => {
			instance = createClassComponent({
				component: Page as never,
				target,
				props: { value: doc(), capabilities: caps, formData: {}, onChange: () => {} }
			});
			flushSync();
		}).not.toThrow();
		cleanup = () => instance?.$destroy();
		await settle();

		const count = () => target.querySelector('[data-testid="preview-count"]')!.textContent;
		const mirrorCalls = () => Number(target.querySelector('[data-testid="mirror-calls"]')!.textContent);
		expect(count()).toBe('0');
		const mountMirrorCalls = mirrorCalls();
		expect(mountMirrorCalls).toBeGreaterThanOrEqual(1);
		expect(mountMirrorCalls).toBeLessThanOrEqual(2);

		const select = target.querySelector<HTMLButtonElement>('button[aria-label="Select for generation"]');
		expect(select).not.toBeNull();
		select!.click();
		await settle();
		expect(count()).toBe('1');
		expect(mirrorCalls()).toBe(mountMirrorCalls + 1);

		// Unrelated page-state churn must not re-fire the mirror.
		instance!.$set({ formData: { references: [] } });
		await settle();
		expect(mirrorCalls()).toBe(mountMirrorCalls + 1);
	});
});
