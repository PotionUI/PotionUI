// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';

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
		getClient: () => ({ get: vi.fn().mockResolvedValue({ data: {} }), post: vi.fn(), put: vi.fn() })
	}
}));

const { flushSync } = await import('svelte');
const { createClassComponent } = await import('svelte/legacy');
const { default: VideoDirectorEditor } = await import('$lib/components/video-director/VideoDirectorEditor.svelte');
const { parseDirectorCapabilities, resolveDirectorRaw } = await import('$lib/utils/videoDirector');
const { applyModelOverlay } = await import('$lib/utils/cloudDirector');

const preset = {
	family: 'cloud',
	preset_modes: ['txt2video', 'img2video'],
	segment_routing: true,
	modes: {
		t2v: {},
		director: { keyframes: 'first_only', max_segments: 6, fps_locked: true, continue_from_video: true, continuation: { source: 'last_frame', overlap_frames: 0, stitch: true } }
	},
	limits: { default_duration: 5, default_fps: 24, max_duration: 10 },
	preset_mode_overrides: { img2video: { modes: { t2v: null, i2v: {}, flf: {} } } }
};

const overlay = {
	label: 'Start',
	raw: { model_label: 'Start', limits: { durations: [3, 5], default_duration: 3, max_duration: 5, default_fps: 24 }, modes: { flf: null, director: { max_frames_per_segment: 120 } } }
};

async function ticks(count = 8) {
	for (let i = 0; i < count; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

describe('the editor when the page hands its own changes back late', () => {
	it('keeps the prompt segment the editor adds while the model arrives', async () => {
		const base = parseDirectorCapabilities(resolveDirectorRaw(preset, 'img2video'))!;
		const hosted = parseDirectorCapabilities(applyModelOverlay(resolveDirectorRaw(preset, 'img2video'), overlay))!;
		const target = document.createElement('div');
		document.body.appendChild(target);
		const emitted: Array<{ chain: { segments: Array<{ duration: number; prompt_segments: unknown[] }> } }> = [];
		const inbox: unknown[] = [];
		const instance = createClassComponent({
			component: VideoDirectorEditor as never,
			target,
			props: {
				value: undefined as unknown,
				capabilities: base,
				presetId: 'p',
				formData: {},
				selectedMode: 'img2video',
				onChange: (next: never) => {
					emitted.push(next);
					inbox.push(next);
				}
			}
		});
		await ticks();
		instance.$set({ capabilities: hosted });
		for (let i = 0; i < 12; i++) {
			await ticks(1);
			const late = inbox.shift();
			if (late) instance.$set({ value: late });
		}
		await ticks();
		const last = emitted[emitted.length - 1];
		expect(target.textContent!.match(/\d+ segments?/)?.[0]).toBe('1 segment');
		expect(target.querySelectorAll('[role="textbox"]').length).toBeGreaterThan(0);
		expect(last.chain.segments[0].prompt_segments.length).toBeGreaterThan(0);
		expect(last.chain.segments[0].duration).toBe(3);
		instance.$destroy();
	});

	it('gives the shot a prompt segment again when the page swaps the film out from under it', async () => {
		const txt = parseDirectorCapabilities(resolveDirectorRaw(preset, 'txt2video'))!;
		const img = parseDirectorCapabilities(resolveDirectorRaw(preset, 'img2video'))!;
		const target = document.createElement('div');
		document.body.appendChild(target);
		let saved: unknown = undefined;
		const instance = createClassComponent({
			component: VideoDirectorEditor as never,
			target,
			props: { value: undefined as unknown, capabilities: txt, presetId: 'p', formData: {}, selectedMode: 'txt2video', onChange: (next: never) => (saved = next) }
		});
		await ticks();
		instance.$set({ value: saved });
		await ticks();
		expect(target.textContent!.match(/\d+ segments?/)?.[0]).toBe('1 segment');
		instance.$set({ value: undefined, capabilities: img, selectedMode: 'img2video' });
		await ticks();
		expect(target.textContent!.match(/\d+ segments?/)?.[0]).toBe('1 segment');
		expect(target.querySelectorAll('[role="textbox"]').length).toBeGreaterThan(0);
		instance.$destroy();
	});
});
