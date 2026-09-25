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
		getClient: () => ({ get: vi.fn(), post: vi.fn() })
	}
}));

const { flushSync } = await import('svelte');
const { createClassComponent } = await import('svelte/legacy');
const { default: VideoDirectorEditor } = await import('$lib/components/video-director/VideoDirectorEditor.svelte');
const { resolveDirectorCapabilities } = await import('$lib/utils/videoDirector');

const LTX_PRESET_RAW = {
	preset_modes: ['video'],
	modes: {
		t2v: {},
		i2v: {},
		flf: {},
		director: { audio: true, ic_lora: true, max_keyframes: 8 }
	},
	limits: { default_duration: 5, default_fps: 25, max_duration: 40, max_frames: 1001 }
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

describe('the Video Director reads film FPS from the generate form, not its own document', () => {
	it('a fresh document uses the form fps for its frame math', async () => {
		const caps = resolveDirectorCapabilities(LTX_PRESET_RAW, 'video')!;
		const target = document.createElement('div');
		document.body.appendChild(target);
		const instance = createClassComponent({
			component: VideoDirectorEditor as never,
			target,
			props: {
				value: undefined,
				capabilities: caps,
				presetId: 'ltx-test',
				formData: { fps: 25 },
				onChange: () => {}
			}
		});
		cleanup = () => instance.$destroy();
		await settle();

		await settle();
		const framesInput = Array.from(target.querySelectorAll<HTMLInputElement>('input')).find((i) => i.value === '125');
		expect(framesInput).toBeTruthy();
	});

	it('changing the form fps on the SAME mounted instance re-derives the frame count live', async () => {
		const caps = resolveDirectorCapabilities(LTX_PRESET_RAW, 'video')!;
		const target = document.createElement('div');
		document.body.appendChild(target);
		const instance = createClassComponent({
			component: VideoDirectorEditor as never,
			target,
			props: {
				value: undefined,
				capabilities: caps,
				presetId: 'ltx-test',
				formData: { fps: 25 },
				onChange: () => {}
			}
		});
		cleanup = () => instance.$destroy();
		await settle();
		await settle();
		expect(Array.from(target.querySelectorAll<HTMLInputElement>('input')).some((i) => i.value === '125')).toBe(true);

		instance.$set({ formData: { fps: 40 } });
		await settle();
		expect(Array.from(target.querySelectorAll<HTMLInputElement>('input')).some((i) => i.value === '200')).toBe(true);
		expect(Array.from(target.querySelectorAll<HTMLInputElement>('input')).some((i) => i.value === '125')).toBe(false);
	});

	it('the shot header has no FPS control any more, and MAX renders as a real button', async () => {
		const caps = resolveDirectorCapabilities(LTX_PRESET_RAW, 'video')!;
		const target = document.createElement('div');
		document.body.appendChild(target);
		const instance = createClassComponent({
			component: VideoDirectorEditor as never,
			target,
			props: {
				value: undefined,
				capabilities: caps,
				presetId: 'ltx-test',
				formData: { fps: 25 },
				onChange: () => {}
			}
		});
		cleanup = () => instance.$destroy();
		await settle();
		await settle();

		expect(target.querySelector('[aria-label="Film frame rate"]')).toBeNull();

		const maxButton = Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Max');
		expect(maxButton).toBeTruthy();
		expect(maxButton!.className).toContain('touch-manipulation');
	});
});
