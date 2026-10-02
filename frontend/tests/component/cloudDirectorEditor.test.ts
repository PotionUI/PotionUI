// @vitest-environment jsdom
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
		getClient: () => ({ get: vi.fn().mockResolvedValue({ data: {} }), post: vi.fn(), put: vi.fn().mockResolvedValue({}) })
	}
}));

const { flushSync } = await import('svelte');
const { createClassComponent } = await import('svelte/legacy');
const { default: VideoDirectorEditor } = await import('$lib/components/video-director/VideoDirectorEditor.svelte');
const { parseDirectorCapabilities, createDefaultDirectorValue } = await import('$lib/utils/videoDirector');
const { applyModelOverlay } = await import('$lib/utils/cloudDirector');
const { nsfwFilterStore } = await import('$lib/stores/nsfwFilter');
const { nsfwRevealStore } = await import('$lib/stores/nsfwReveal');

import type { ChainSegment, DirectorCapabilities, VideoDirectorValue } from '$lib/types/videoDirector';
import type { DirectorRunState } from '$lib/types/tabs';

const presetBlock = {
	preset_modes: ['txt2video', 'img2video'],
	segment_routing: true,
	modes: {
		t2v: {},
		i2v: {},
		flf: {},
		director: { keyframes: 'first_only', max_segments: 8, audio: true, continue_from_video: true, continuation: { source: 'last_frame', overlap_frames: 0, stitch: true } }
	},
	limits: { default_duration: 4, default_fps: 24, max_duration: 15 }
};

const liteOverlay: { label: string; raw: Record<string, unknown> } = {
	label: 'Fake Lite',
	raw: {
		modes: { flf: null, director: { max_segments: 3, audio: false } },
		limits: { default_duration: 4, default_fps: 24, max_duration: 8, durations: [4, 6, 8] }
	}
};

function capsFor(overlay: typeof liteOverlay | null): DirectorCapabilities {
	return parseDirectorCapabilities(applyModelOverlay(presetBlock, overlay))!;
}

function segment(id: string, prompt: string, duration = 4): ChainSegment {
	return {
		id,
		prompt,
		prompt_segments: [],
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

function film(caps: DirectorCapabilities, segments: ChainSegment[]): VideoDirectorValue {
	const doc = createDefaultDirectorValue(caps);
	return { ...doc, mode: 'director', chain: { ...doc.chain, segments } };
}

function run(overrides: Partial<DirectorRunState>): DirectorRunState {
	return { generationId: 'gen-1', status: 'queued', progress: null, finishedAt: null, posterUrl: null, inputsHash: null, ...overrides };
}

async function settle() {
	for (let i = 0; i < 5; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

let cleanup: (() => void) | undefined;

afterEach(async () => {
	await nsfwFilterStore.setMode('blur');
	nsfwRevealStore.reset();
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

async function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({
		component: VideoDirectorEditor as never,
		target,
		props: { presetId: 'cloud-video', formData: {}, onChange: () => {}, ...props }
	});
	cleanup = () => instance.$destroy();
	await settle();
	return target;
}

describe('Video Director editor for a hosted model', () => {
	it('names the model and what it can do', async () => {
		const caps = capsFor(liteOverlay);
		const target = await mount({ value: film(caps, [segment('a', 'one'), segment('b', 'two')]), capabilities: caps });
		const limits = target.querySelector('[data-model-limits]');
		expect(limits).toBeTruthy();
		expect(limits!.querySelector('[data-model-name]')!.textContent).toBe('Fake Lite');
		const text = limits!.textContent!;
		expect(text).toContain('Starts from a picture');
		expect(text).toContain('No end picture');
		expect(text).toContain('Shots of 4, 6, 8 s');
		expect(text).toContain('Up to 3 shots');
		expect(text).toContain('No audio');
		expect(target.querySelector('[data-model-notices]')).toBeNull();
	});

	it('explains what would not be used instead of dropping it', async () => {
		const caps = capsFor(liteOverlay);
		const four = film(caps, [segment('a', 'one'), segment('b', 'two'), segment('c', 'three'), segment('d', 'four', 5)]);
		const target = await mount({ value: four, capabilities: caps });
		const notices = target.querySelector('[data-model-notices]')!.textContent!;
		expect(notices).toContain('Fake Lite makes up to 3 shots in one film. Your film has 4, so shot 4 would not be made.');
		expect(notices).toContain('Fake Lite renders shots of 4, 6 and 8 s. Shot 4 is a different length.');
		expect(target.textContent).toContain('Change these, or pick another model, to generate.');
	});

	it('offers to set the shot lengths the model renders and applies it', async () => {
		const caps = capsFor(liteOverlay);
		const onChange = vi.fn();
		const target = await mount({ value: film(caps, [segment('a', 'one', 5)]), capabilities: caps, onChange });
		const fix = [...target.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Use lengths it renders');
		expect(fix).toBeTruthy();
		fix!.click();
		const sent = onChange.mock.calls.map((c) => c[0]).find((v) => v.chain.segments[0].duration === 4);
		expect(sent).toBeTruthy();
	});

	it('offers no length fix when only the shot count is too high', async () => {
		const caps = capsFor(liteOverlay);
		const four = film(caps, [segment('a', 'one'), segment('b', 'two'), segment('c', 'three'), segment('d', 'four')]);
		const target = await mount({ value: four, capabilities: caps });
		const labels = [...target.querySelectorAll('button')].map((b) => b.textContent?.trim());
		expect(labels).not.toContain('Use lengths it renders');
	});

	it('shows nothing extra for a preset without a hosted model', async () => {
		const caps = capsFor(null);
		const target = await mount({ value: film(caps, [segment('a', 'one')]), capabilities: caps });
		expect(target.querySelector('[data-model-limits]')).toBeNull();
	});

	it('shows each shot in its own state with the reason and a retry from the failed one', async () => {
		const caps = capsFor(liteOverlay);
		const onGenerateShots = vi.fn();
		const runs = {
			a: run({ status: 'done', finishedAt: new Date(2026, 0, 1, 9, 5).getTime(), posterUrl: '/clips/a.mp4', outputPath: 'g/a.mp4' }),
			b: run({ status: 'failed', message: 'The provider refused this prompt.' }),
			c: run({ status: 'failed', message: 'Not made because an earlier shot did not finish.' })
		};
		const target = await mount({
			value: film(caps, [segment('a', 'one'), segment('b', 'two'), segment('c', 'three')]),
			capabilities: caps,
			runs,
			onGenerateShots
		});
		const states = [...target.querySelectorAll('[data-run-state]')].map((el) => el.getAttribute('data-run-state'));
		expect(states).toEqual(['done', 'failed', 'failed']);
		const messages = [...target.querySelectorAll('[data-run-message]')].map((el) => el.textContent);
		expect(messages).toContain('The provider refused this prompt.');
		expect(messages).toContain('Not made because an earlier shot did not finish.');
		const retry = [...target.querySelectorAll('button')].filter((b) => b.textContent?.trim() === 'Retry from here');
		expect(retry).toHaveLength(2);
		retry[0].click();
		expect(onGenerateShots).toHaveBeenCalledWith(['b']);
	});

	it('shows the shot that is generating with its own percentage', async () => {
		const caps = capsFor(liteOverlay);
		const runs = {
			a: run({ status: 'done', finishedAt: 1000 }),
			b: run({ status: 'generating', progress: 0.4 }),
			c: run({ status: 'queued' })
		};
		const target = await mount({
			value: film(caps, [segment('a', 'one'), segment('b', 'two'), segment('c', 'three')]),
			capabilities: caps,
			runs
		});
		const text = target.textContent!;
		expect(text).toContain('Generating 40%');
		expect(text).toContain('Queued');
		expect(text).toMatch(/Done · \d\d:\d\d/);
	});

	it('keeps Retry as the plain label for a native preset', async () => {
		const caps = capsFor(null);
		const target = await mount({
			value: film(caps, [segment('a', 'one'), segment('b', 'two')]),
			capabilities: caps,
			runs: { b: run({ status: 'failed' }) }
		});
		const labels = [...target.querySelectorAll('button')].map((b) => b.textContent?.trim());
		expect(labels).toContain('Retry');
		expect(labels).not.toContain('Retry from here');
	});

	function clipRuns(overrides: Partial<DirectorRunState>) {
		return {
			a: run({ status: 'done', posterUrl: '/clips/a.mp4', outputPath: 'g/a.mp4' }),
			b: run({ status: 'done', posterUrl: '/clips/b.mp4', outputPath: 'g/b.mp4', ...overrides }),
			c: run({ status: 'queued' })
		};
	}

	async function mountClips(runs: Record<string, DirectorRunState>) {
		const caps = capsFor(liteOverlay);
		return mount({ value: film(caps, [segment('a', 'one'), segment('b', 'two'), segment('c', 'three')]), capabilities: caps, runs });
	}

	it('shows a finished clip as the thumbnail of its shot', async () => {
		const target = await mountClips(clipRuns({}));
		const thumbs = target.querySelectorAll('.row-thumb video');
		expect(thumbs).toHaveLength(1);
		expect(thumbs[0].getAttribute('src')).toBe('/clips/b.mp4');
		expect(target.querySelector('[data-thumb-blurred]')).toBeNull();
	});

	it('blurs a flagged clip until the person reveals it', async () => {
		const target = await mountClips(clipRuns({ flagged: true }));
		expect(target.querySelector('[data-thumb-blurred]')).toBeTruthy();
		expect(target.querySelector('.row-thumb video')!.getAttribute('class')).toContain('blur-2xl');
		const reveal = target.querySelector('button[aria-label="Sensitive content, click to reveal"]') as HTMLButtonElement;
		expect(reveal).toBeTruthy();
		reveal.click();
		await settle();
		expect(target.querySelector('[data-thumb-blurred]')).toBeNull();
		expect(target.querySelector('.row-thumb video')!.getAttribute('class')).not.toContain('blur-2xl');
	});

	it('hides a flagged clip when the person chose to hide sensitive content', async () => {
		await nsfwFilterStore.setMode('hide');
		const target = await mountClips(clipRuns({ flagged: true }));
		expect(target.querySelector('[data-thumb-hidden]')).toBeTruthy();
		expect(target.querySelector('.row-thumb video')).toBeNull();
		expect(target.innerHTML).not.toContain('/clips/b.mp4');
	});

	it('shows no clip at all when the shot was not saved', async () => {
		const target = await mountClips(clipRuns({ posterUrl: null, flagged: true }));
		expect(target.querySelector('.row-thumb video')).toBeNull();
		expect(target.innerHTML).not.toContain('/clips/b.mp4');
	});

	it('offers to redo the film, and says why, for a model that cannot continue from a clip', async () => {
		const caps = capsFor({
			label: 'Fake Text',
			raw: { modes: { flf: null, director: { max_segments: 3, continue_from_video: false } }, limits: { default_duration: 4, default_fps: 24, max_duration: 8, durations: [4, 6, 8] } }
		});
		const onGenerateShots = vi.fn();
		const runs = { a: run({ status: 'done', outputPath: 'g/a.mp4', posterUrl: '/clips/a.mp4' }), b: run({ status: 'failed', message: 'Refused.' }) };
		const target = await mount({
			value: film(caps, [segment('a', 'one'), segment('b', 'two')]),
			capabilities: caps,
			runs,
			onGenerateShots
		});
		const labels = [...target.querySelectorAll('button')].map((b) => b.textContent?.trim());
		expect(labels).toContain('Redo the film');
		expect(labels).not.toContain('Retry from here');
		expect(target.querySelector('[data-retry-note]')!.textContent).toBe(
			'Fake Text cannot carry on from a finished shot, so the whole film is made again from the first shot.'
		);
	});

	it('opens a blank film on the length the model renders and saves it', async () => {
		const caps = capsFor(liteOverlay);
		const onChange = vi.fn();
		const blank = film(capsFor(null), [{ ...segment('a', '', 5) }]);
		const target = await mount({ value: blank, capabilities: caps, onChange });
		expect(target).toBeTruthy();
		const saved = onChange.mock.calls.map((c) => c[0]).pop();
		expect(saved.chain.segments[0].duration).toBe(4);
	});

	it('keeps a length the person chose and says so', async () => {
		const caps = capsFor(liteOverlay);
		const onChange = vi.fn();
		const target = await mount({ value: film(caps, [segment('a', 'one', 5), segment('b', 'two', 4)]), capabilities: caps, onChange });
		expect(target.querySelector('[data-model-notices]')!.textContent).toContain('renders shots of 4, 6 and 8 s');
		expect(onChange.mock.calls.some((c) => c[0].chain.segments[0].duration !== 5)).toBe(false);
	});
});
