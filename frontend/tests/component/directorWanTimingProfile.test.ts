// @vitest-environment jsdom
//
// DIR-06 rework: proves the Wan live timing profile actually reaches the
// rendered Video Director editor, not just the pure `railModel.ts` functions
// (see railModel.test.ts's own "known timing profile" describe block for the
// pure-function half of this proof). Mounts the real VideoDirectorEditor ->
// ShotConsole -> deriveConsoleModel -> deriveRailModel chain against a
// preset-shaped Wan capability block (parsed through the real
// resolveDirectorCapabilities, so this also exercises `timing` capability
// parsing itself) and a `formData` carrying `svi_motion_latent_count` --
// the exact 80/80/80 film Codex's production probe and the backend's own
// tests/pipelines/pipes/generator/chain_video_wan22/test_geometry.py check
// against the Python module: contributions [81,77,81] (motion 2) / [81,80,81]
// (motion 1).
import { describe, it, expect, afterEach, vi } from 'vitest';

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
const { default: VideoDirectorEditor } = await import('$lib/components/video-director/VideoDirectorEditor.svelte');
const { resolveDirectorCapabilities } = await import('$lib/utils/videoDirector');

import type { VideoDirectorValue, ChainSegment } from '$lib/types/videoDirector';

// Mirrors content/presets/marketplace/Wan/preset.yml's `video_director` block
// exactly (family/timing/segment_routing/modes.director), so this test also
// proves `parseDirectorCapabilities` reads the `timing` block correctly.
const WAN_PRESET_RAW = {
	family: 'wan',
	timing: { motion_latent_count_field: 'svi_motion_latent_count', motion_latent_count_default: 1 },
	preset_modes: ['video'],
	segment_routing: true,
	modes: {
		t2v: {},
		i2v: {},
		flf: {},
		director: {
			per_segment_loras: true,
			keyframes: 'first_only',
			max_segments: 8,
			max_frames_per_segment: 81,
			continuation: { source: 'tail_frames', overlap_frames: 4, stitch: true }
		}
	},
	limits: { default_duration: 5, default_fps: 16, max_duration: 60 }
};

function blankChainSegment(id: string, duration: number, overrides: Partial<ChainSegment> = {}): ChainSegment {
	return {
		id,
		prompt: `shot ${id}`,
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

function wan808080Doc(overrides: { overlapFrames?: number; stitch?: boolean } = {}): VideoDirectorValue {
	return {
		schema_version: 1,
		mode: 'director',
		global_prompt: '',
		global_prompt_segments: [],
		negative_prompt: '',
		negative_prompt_segments: [],
		simple: { duration: 5, fps: 16, start_image: null, first_frame: null, last_frame: null },
		timeline: {
			fps: 16,
			shots: [{ id: 'shot-1', duration: 5, continue_from_previous: false, segments: [], keyframes: [], audio: [], ic_lora: [] }]
		},
		chain: {
			fps: 16,
			segments: [
				blankChainSegment('seg-a', 80 / 16, { sub_type_override: 't2v' }),
				blankChainSegment('seg-b', 80 / 16),
				blankChainSegment('seg-c', 80 / 16, { sub_type_override: 't2v' })
			],
			continuation: { overlap_frames: overrides.overlapFrames ?? 4, stitch: overrides.stitch ?? true },
			keyframes: [],
			audio: []
		}
	};
}

async function settle() {
	for (let i = 0; i < 5; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

// The fixture's segments carry an empty `prompt_segments`, so normalization
// re-derives an empty prompt and `deriveShotLabel` falls back to "Shot N" --
// seg-b is the film's 2nd segment, hence "Expand Shot 2".
function expandShotB(target: HTMLDivElement) {
	const shotB = target.querySelector('button[aria-label="Expand Shot 2"]') as HTMLButtonElement | null;
	expect(shotB).toBeTruthy();
	shotB!.click();
}

let cleanup: (() => void) | undefined;

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('Video Director editor renders the live Wan timing profile end to end', () => {
	it('motion 2 (svi_motion_latent_count=2): header total 14.9s, shot-b card shows 81 frames / 77 new', async () => {
		const caps = resolveDirectorCapabilities(WAN_PRESET_RAW, 'video')!;
		const target = document.createElement('div');
		document.body.appendChild(target);
		const instance = createClassComponent({
			component: VideoDirectorEditor as never,
			target,
			props: {
				value: wan808080Doc(),
				capabilities: caps,
				presetId: 'wan-test',
				formData: { svi_motion_latent_count: 2 },
				onChange: () => {}
			}
		});
		cleanup = () => instance.$destroy();
		await settle();

		// 239 emitted frames / 16 fps = 14.9375s -> "14.9 s" (ConsoleHeader.svelte's own .toFixed(1))
		expect(target.textContent).toContain('14.9 s');

		expandShotB(target);
		await settle();
		expect(target.textContent).toContain('81');
		expect(target.textContent).toContain('77 new');
	});

	it('motion 1 (the absent-config fallback): header total 15.1s, shot-b card shows 80 new', async () => {
		const caps = resolveDirectorCapabilities(WAN_PRESET_RAW, 'video')!;
		const target = document.createElement('div');
		document.body.appendChild(target);
		const instance = createClassComponent({
			component: VideoDirectorEditor as never,
			target,
			props: {
				value: wan808080Doc(),
				capabilities: caps,
				presetId: 'wan-test',
				formData: { svi_motion_latent_count: 1 },
				onChange: () => {}
			}
		});
		cleanup = () => instance.$destroy();
		await settle();

		// 242 / 16 = 15.125 -> "15.1 s"
		expect(target.textContent).toContain('15.1 s');
		expandShotB(target);
		await settle();
		expect(target.textContent).toContain('80 new');
	});

	it('changing the sibling svi_motion_latent_count value on the SAME mounted instance updates the rail live', async () => {
		const caps = resolveDirectorCapabilities(WAN_PRESET_RAW, 'video')!;
		const target = document.createElement('div');
		document.body.appendChild(target);
		const instance = createClassComponent({
			component: VideoDirectorEditor as never,
			target,
			props: {
				value: wan808080Doc(),
				capabilities: caps,
				presetId: 'wan-test',
				formData: { svi_motion_latent_count: 2 },
				onChange: () => {}
			}
		});
		cleanup = () => instance.$destroy();
		await settle();
		expandShotB(target);
		await settle();
		expect(target.textContent).toContain('77 new');

		instance.$set({ formData: { svi_motion_latent_count: 1 } });
		await settle();
		expect(target.textContent).toContain('80 new');
		expect(target.textContent).not.toContain('77 new');
	});

	// The "reopened document" case used to live here as a synthetic
	// `chain.timingProfile` fixture. That field is informational only now
	// (see `resolveDirectorTimingProfile`'s doc comment) -- the REAL
	// round-trip proof, through the actual `collectTabSessionData`/
	// `buildSessionRestoreTabPatch` tab-session machinery, lives in
	// `src/lib/utils/directorTimingSessionRestore.test.ts`.

	it('an unqualified Wan shot (no timing capability at all) shows the raw duration with a "requested" qualifier', async () => {
		const noTimingRaw = { ...WAN_PRESET_RAW, timing: undefined };
		const caps = resolveDirectorCapabilities(noTimingRaw, 'video')!;
		expect(caps.timing).toBeNull();
		const target = document.createElement('div');
		document.body.appendChild(target);
		const instance = createClassComponent({
			component: VideoDirectorEditor as never,
			target,
			props: {
				value: wan808080Doc(),
				capabilities: caps,
				presetId: 'wan-test',
				formData: { svi_motion_latent_count: 2 },
				onChange: () => {}
			}
		});
		cleanup = () => instance.$destroy();
		await settle();
		// The header's own film-total qualifier -- visible immediately, no
		// need to expand a shot. Scoped to the <header> element specifically:
		// the default-expanded first shot's OWN ShotCard qualifier would also
		// satisfy a page-wide "requested" search, so this must check the
		// header in isolation to actually prove ConsoleHeader renders one.
		expect(target.querySelector('header')?.textContent).toContain('requested');
		expandShotB(target);
		await settle();
		expect(target.textContent).toContain('requested');
		expect(target.textContent).not.toContain('77 new');
	});

	it('a qualified motion-2 session shows no "requested" marker anywhere, including the header total', async () => {
		const caps = resolveDirectorCapabilities(WAN_PRESET_RAW, 'video')!;
		const target = document.createElement('div');
		document.body.appendChild(target);
		const instance = createClassComponent({
			component: VideoDirectorEditor as never,
			target,
			props: {
				value: wan808080Doc(),
				capabilities: caps,
				presetId: 'wan-test',
				formData: { svi_motion_latent_count: 2 },
				onChange: () => {}
			}
		});
		cleanup = () => instance.$destroy();
		await settle();
		expect(target.textContent).toContain('14.9 s');
		expect(target.textContent).not.toContain('requested');
	});

	it('stitch: false renders the untrimmed segment\'s full on-disk length, not the stitched-join amount', async () => {
		const caps = resolveDirectorCapabilities(WAN_PRESET_RAW, 'video')!;
		const doc = wan808080Doc({ stitch: false });
		// Shrink seg-b to a short (untrimmed) window so stitch actually
		// changes the numbers -- see resolveWanSegmentGeometry's own
		// stitch=false unit tests for why the 80/80/80 film alone doesn't.
		doc.chain = { ...doc.chain, segments: doc.chain.segments.map((s) => (s.id === 'seg-b' ? { ...s, duration: 5 / 16 } : s)) };
		const target = document.createElement('div');
		document.body.appendChild(target);
		const instance = createClassComponent({
			component: VideoDirectorEditor as never,
			target,
			props: {
				value: doc,
				capabilities: caps,
				presetId: 'wan-test',
				formData: { svi_motion_latent_count: 2 },
				onChange: () => {}
			}
		});
		cleanup = () => instance.$destroy();
		await settle();
		expandShotB(target);
		await settle();
		// seg-b: frames snap(5)=5, context=min(4,81)=4, 5 is not > 4+1 ->
		// untrimmed; stitch=false means no join drop -> full 5 frames, no
		// "new" overlap-in figure at all (hasOverlapIn still true, but
		// newFrames === contributedFrames === totalFrames here).
		// The frame count is an editable input in the shot header; only the
		// cap and the overlap readout are text.
		const framesInput = Array.from(target.querySelectorAll<HTMLInputElement>('input')).find((i) => i.value === '5');
		expect(framesInput).toBeTruthy();
		expect(target.textContent).toContain('/ 81 frames');
		expect(target.textContent).toContain('5 new');
		expect(target.textContent).not.toContain('1 new');
	});
});
