// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { settleFlush } from './helpers/settle';

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
		setOnTokenRefreshed: vi.fn(),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getClient: () => ({ get: vi.fn().mockResolvedValue({ data: { data: {} } }), post: vi.fn() })
	}
}));

const { createClassComponent } = await import('svelte/legacy');
const { default: VideoDirectorEditor } = await import('$lib/components/video-director/VideoDirectorEditor.svelte');
const { resolveDirectorCapabilities, createDefaultDirectorValue } = await import('$lib/utils/videoDirector');

import type { VideoDirectorValue } from '$lib/types/videoDirector';

const H3_RAW = {
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
			reference_fields: ['references'],
			modes: { director: { keyframes: null, audio: false, continuation: null, max_overlap_frames: null } }
		}
	}
};

let cleanup: (() => void) | undefined;

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('Video Director selection across preset modes', () => {
	it('shows the shot prompt in H3 References after a start frame was selected in Video', async () => {
		const videoCaps = resolveDirectorCapabilities(H3_RAW, 'video')!;
		const refsCaps = resolveDirectorCapabilities(H3_RAW, 'refs')!;
		let stored: VideoDirectorValue = createDefaultDirectorValue(videoCaps);

		const target = document.createElement('div');
		document.body.appendChild(target);
		const instance = createClassComponent({
			component: VideoDirectorEditor as never,
			target,
			props: {
				value: stored,
				capabilities: videoCaps,
				selectedMode: 'video',
				presetId: 'h3',
				formData: {},
				onChange: (v: VideoDirectorValue) => {
					stored = v;
				}
			}
		});
		cleanup = () => instance.$destroy();
		await settleFlush();

		expect(target.querySelector('[contenteditable="true"]')).toBeTruthy();
		const start = target.querySelector('.kf-anchor') as HTMLElement | null;
		expect(start).toBeTruthy();
		start!.click();
		await settleFlush();
		expect(target.querySelector('[contenteditable="true"]')).toBeNull();

		instance.$set({ capabilities: refsCaps, selectedMode: 'refs', value: stored });
		await settleFlush();

		expect(target.querySelector('.kf-anchor')).toBeNull();
		expect(target.textContent).not.toContain('Drop an image');
		const editor = target.querySelector('[contenteditable="true"]');
		expect(editor).toBeTruthy();
	});
});
