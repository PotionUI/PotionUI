// @vitest-environment jsdom
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
const { default: VideoDirectorEditor } = await import('$lib/components/video-director/VideoDirectorEditor.svelte');

import type { VideoDirectorValue, DirectorCapabilities, DirectorModeCapability, ChainSegment } from '$lib/types/videoDirector';

function baseModeCap(overrides: Partial<DirectorModeCapability> = {}): DirectorModeCapability {
	return {
		tips: [],
		maxDuration: null,
		audio: false,
		icLora: false,
		maxKeyframes: null,
		perSegmentLoras: false,
		keyframes: 'first_only',
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

function chainCaps(): DirectorCapabilities {
	return {
		presetModes: null,
		modes: { director: baseModeCap({ maxSegments: 8 }) },
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

function chainSegment(id: string, prompt: string, duration: number): ChainSegment {
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
		steps: null,
		cfg: null
	};
}

function docWith(globalPrompt: string, negativePrompt: string): VideoDirectorValue {
	return {
		schema_version: 1,
		mode: 'director',
		global_prompt: globalPrompt,
		global_prompt_segments: globalPrompt
			? [{ id: 'g0', content: globalPrompt, chips: {}, type: 'content', enabled: true }]
			: [],
		negative_prompt: negativePrompt,
		negative_prompt_segments: negativePrompt
			? [{ id: 'n0', content: negativePrompt, chips: {}, type: 'content', enabled: true }]
			: [],
		simple: { duration: 5, fps: 24, start_image: null, first_frame: null, last_frame: null },
		timeline: {
			fps: 24,
			shots: [{ id: 'shot-1', duration: 5, continue_from_previous: false, segments: [], keyframes: [], audio: [], ic_lora: [] }]
		},
		chain: {
			fps: 16,
			segments: [chainSegment('chain-1', 'Artisan at work', 5)],
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

function mountEditor(value: VideoDirectorValue) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	let storedValue = value;
	let onChangeCalls = 0;
	const instance = createClassComponent({
		component: VideoDirectorEditor as never,
		target,
		props: {
			value,
			capabilities: chainCaps(),
			presetId: 'test-preset',
			formData: null,
			onChange: (v: VideoDirectorValue) => {
				onChangeCalls += 1;
				storedValue = v;
			}
		}
	});
	return {
		target,
		latest: () => storedValue,
		onChangeCalls: () => onChangeCalls,
		destroy: () => instance.$destroy()
	};
}

let mounted: ReturnType<typeof mountEditor> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

function globalPromptButton(target: HTMLElement): HTMLButtonElement {
	const button = Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Global prompt'));
	expect(button).toBeTruthy();
	return button as HTMLButtonElement;
}

describe('VideoDirectorEditor header: Global prompt button', () => {
	it('renders as a plain button with no badge when both fields are empty', async () => {
		mounted = mountEditor(docWith('', ''));
		await settle();

		const button = globalPromptButton(mounted.target);
		expect(button.querySelector('.bg-signal\\/15')).toBeNull();
	});

	it('shows a count badge once either field has content', async () => {
		mounted = mountEditor(docWith('A cinematic teaser\nsecond line', 'blurry, warped hands'));
		await settle();

		const button = globalPromptButton(mounted.target);
		const badge = button.querySelector('.bg-signal\\/15');
		expect(badge?.textContent?.trim()).toBe('2');
	});
});

describe('VideoDirectorEditor header: Global prompt panel', () => {
	it('opens both segmented editors, labeled, on click', async () => {
		mounted = mountEditor(docWith('A cinematic teaser', 'blurry hands'));
		await settle();

		expect(document.body.querySelector('[role="dialog"]')).toBeNull();
		globalPromptButton(mounted.target).click();
		await settle();

		const dialog = document.body.querySelector('[role="dialog"]');
		expect(dialog).toBeTruthy();
		expect(dialog!.querySelector('[role="list"][aria-label="Global prompt"]')).toBeTruthy();
		expect(dialog!.querySelector('[role="list"][aria-label="Negative prompt"]')).toBeTruthy();
	});

	it('renders the full image-tab toolbar row (Prompts/Segments/Templates + More) for each editor, not the stripped-down embedded rail', async () => {
		mounted = mountEditor(docWith('A cinematic teaser', 'blurry hands'));
		await settle();

		globalPromptButton(mounted.target).click();
		await settle();

		const dialog = document.body.querySelector('[role="dialog"]') as HTMLElement;
		const buttonLabels = Array.from(dialog.querySelectorAll('button')).map((button) => button.textContent?.trim());
		expect(buttonLabels).toEqual(expect.arrayContaining(['Prompts', 'Segments', 'Templates']));
		expect(dialog.querySelectorAll('[aria-label="More prompt actions"]').length).toBe(2);
	});

	it('a typed edit in the panel reaches onChange', async () => {
		mounted = mountEditor(docWith('A cinematic teaser', ''));
		await settle();

		globalPromptButton(mounted.target).click();
		await settle();

		const editable = document.body.querySelector('[role="list"][aria-label="Global prompt"] [contenteditable="true"]') as HTMLElement | null;
		expect(editable).toBeTruthy();
		editable!.textContent = 'A cinematic teaser, golden hour';
		editable!.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		expect(mounted.onChangeCalls()).toBeGreaterThan(0);
		expect(mounted.latest().global_prompt).toContain('golden hour');
	});
});

describe('ShotStage read-only Global prompt note', () => {
	function selectChainBeat(target: HTMLElement) {
		const beat = target.querySelector('.beat-b') as HTMLElement | null;
		expect(beat).toBeTruthy();
		beat!.click();
	}

	it('shows the global (and negative) text, muted, above the beat, once a shot is selected', async () => {
		mounted = mountEditor(docWith('A cinematic teaser', 'blurry hands'));
		await settle();
		selectChainBeat(mounted.target);
		await settle();

		const note = mounted.target.querySelector('.global-note');
		expect(note).toBeTruthy();
		expect(note!.textContent).toContain('Global prompt');
		expect(note!.textContent).toContain('Attached to every shot');
		expect(note!.textContent).toContain('A cinematic teaser');
		expect(note!.textContent).toContain('blurry hands');
	});

	it('is hidden once both the global and negative prompt are empty', async () => {
		mounted = mountEditor(docWith('', ''));
		await settle();
		selectChainBeat(mounted.target);
		await settle();

		expect(mounted.target.querySelector('.global-note')).toBeNull();
	});

	it('its Edit link opens the same header panel', async () => {
		mounted = mountEditor(docWith('A cinematic teaser', ''));
		await settle();
		selectChainBeat(mounted.target);
		await settle();

		const editLink = mounted.target.querySelector('.global-note .edit-link') as HTMLButtonElement | null;
		expect(editLink).toBeTruthy();
		expect(document.body.querySelector('[role="dialog"]')).toBeNull();
		editLink!.click();
		await settle();

		expect(document.body.querySelector('[role="dialog"]')).toBeTruthy();
	});
});
