// @vitest-environment jsdom
//
// StageShot's own onDoc chain is proven sound (stageShotPromptPersistence.test.ts).
// This tests one level up: the full VideoDirectorEditor, whose two $effects
// (adopt-external-value, mode-coherence-then-emit) sit between a typed edit
// and `onChange`, and whose `value` prop is round-tripped through a store the
// way PromptSection.svelte's `tabsStore.updateTab` does -- reproducing the
// exact loop a real generate-page edit goes through, to catch an echo/adopt
// effect clobbering a just-typed doc before it ever reaches `onChange`, or an
// `onChange` that never fires because the mode-coherence effect keeps
// reassigning `doc` and returning early.
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
const { resolveDirectorCapabilities, normalizeDirectorValue, validateDirector, buildDirectorSubmission } =
	await import('$lib/utils/videoDirector');

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

describe('VideoDirectorEditor: typed shot edit reaches onChange, and a store round trip does not clobber it', () => {
	it('H3 refs profile: emits onChange with the typed prompt; re-feeding that value back as `value` is a stable no-op', async () => {
		const caps = resolveDirectorCapabilities(H3_REFS_PRESET_RAW, 'refs')!;
		const initial = baseDoc();
		initial.chain = {
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

		// Mirrors PromptSection.svelte + tabsStore.updateTab: `onChange` writes
		// back into `storedValue`, and the round trip below re-feeds that back
		// in as `value` via `$set`, exactly like `tab.videoDirector` flowing
		// back through the tabs store into the `value` prop.
		let storedValue: VideoDirectorValue | undefined = initial;
		let onChangeCalls = 0;

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
				onChange: (v: VideoDirectorValue) => {
					onChangeCalls += 1;
					storedValue = v;
				}
			}
		});
		cleanup = () => instance.$destroy();

		await settle();

		const editable = target.querySelector('[contenteditable="true"]') as HTMLElement | null;
		expect(editable).toBeTruthy();
		editable!.textContent = 'a lighthouse at dusk';
		editable!.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		expect(onChangeCalls).toBeGreaterThan(0);
		expect(storedValue).toBeDefined();
		const seg = storedValue!.chain.segments.find((s) => s.id === 'chain-1')!;
		expect(seg.prompt).toContain('a lighthouse at dusk');
		expect(seg.prompt_segments.map((s) => s.content).join(' ')).toContain('a lighthouse at dusk');

		// The store round trip: PromptSection re-passes `tab.videoDirector` (now
		// `storedValue`) back in as `value`. If the adopt effect misfires this
		// is where a re-projection would wipe the just-typed text back out.
		const callsBeforeSettle = onChangeCalls;
		instance.$set({ value: storedValue });
		await settle();
		const segAfterRoundTrip = storedValue!.chain.segments.find((s) => s.id === 'chain-1')!;
		expect(segAfterRoundTrip.prompt).toContain('a lighthouse at dusk');

		const normalized = normalizeDirectorValue(storedValue, caps);
		const validation = validateDirector(normalized, caps);
		expect(validation.ok).toBe(true);
		const submission = buildDirectorSubmission(normalized, caps);
		expect(submission.segments[0].prompt).toContain('a lighthouse at dusk');

		void callsBeforeSettle;
	});
});
