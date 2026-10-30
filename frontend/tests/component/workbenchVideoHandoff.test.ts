// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { settle } from './helpers/settle';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationById: vi.fn(),
		getGenerationParams: vi.fn(),
		updateGenerationTags: vi.fn(),
		getBaseURL: () => 'http://localhost',
		getToken: () => null,
		setOnAuthExpired: vi.fn(),
		setOnTokenRefreshed: vi.fn(),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getClient: () => ({ get: vi.fn(), post: vi.fn() })
	}
}));

vi.mock('$app/stores', () => {
	const { writable } = require('svelte/store');
	return { page: writable({ url: new URL('http://localhost/generate') }) };
});

class FakeResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

const { default: Workbench } = await import('$lib/components/Workbench.svelte');
const { createClassComponent } = await import('svelte/legacy');

const PREVIEW = 'data:image/png;base64,last-preview';
const VIDEO_URL = '/api/media/generations/gen-1/0.mp4';

let originalRO: unknown;
let target: HTMLElement;
let component: { $destroy: () => void; $set: (props: Record<string, unknown>) => void } | undefined;

beforeEach(() => {
	originalRO = (globalThis as any).ResizeObserver;
	(globalThis as any).ResizeObserver = FakeResizeObserver;
	target = document.createElement('div');
	document.body.appendChild(target);
});

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target.remove();
	(globalThis as any).ResizeObserver = originalRO;
	vi.clearAllMocks();
});

const generating = {
	id: 'gen-1',
	generation_id: 'gen-1',
	status: 'running',
	file_type: 'video',
	current_image: PREVIEW
};
const completed = { id: 'gen-1', generation_id: 'gen-1', status: 'completed', file_type: 'video', current_image: null };
const previewImg = () => target.querySelector(`img[src="${PREVIEW}"]`);
const video = () => target.querySelector('[data-video-stage] video');
const stage = () => target.querySelector<HTMLElement>('[data-video-stage]');

function mountGenerating() {
	component = createClassComponent({
		component: Workbench as never,
		target,
		props: {
			emptyLayout: 'pane',
			currentGeneration: generating,
			isGenerating: true,
			workbenchIndex: 0,
			workbenchTotal: 0,
			batchImages: [],
			batchVideos: [],
			batchAudios: [],
			batchMeshes: []
		}
	}) as unknown as typeof component;
}

const hold = () => target.querySelector<HTMLElement>('[data-preview-hold]');
const holdImg = () => hold()?.querySelector('img') ?? null;

describe('Workbench video result landing', () => {
	it('keeps one preview image on top, over a loading player, until the player has a frame', async () => {
		mountGenerating();
		await settle(10);
		expect(holdImg()?.getAttribute('src')).toBe(PREVIEW);
		const overlayImage = holdImg();

		component!.$set({ currentGeneration: { ...completed, current_image: PREVIEW }, isGenerating: false });
		await settle(10);
		expect(holdImg()).toBe(overlayImage);
		expect(video()).toBeNull();

		component!.$set({
			currentGeneration: completed,
			workbenchTotal: 1,
			batchVideos: [{ url: VIDEO_URL, originalUrl: VIDEO_URL, file_type: 'video' }]
		});
		await settle(10);

		expect(holdImg()).toBe(overlayImage);
		expect(video()).not.toBeNull();
		expect(stage()!.className).toBe('contents');
		const player = video();

		video()!.dispatchEvent(new Event('loadeddata'));
		await settle(10);

		expect(hold()).toBeNull();
		expect(video()).toBe(player);
	});

	it('releases the preview when the player errors instead of holding it', async () => {
		mountGenerating();
		await settle(10);
		component!.$set({
			currentGeneration: completed,
			isGenerating: false,
			workbenchTotal: 1,
			batchVideos: [{ url: VIDEO_URL, originalUrl: VIDEO_URL, file_type: 'video' }]
		});
		await settle(10);
		expect(hold()).not.toBeNull();

		video()!.dispatchEvent(new Event('error'));
		await settle(10);

		expect(hold()).toBeNull();
	});

	it('clears the hold when the generation fails', async () => {
		mountGenerating();
		await settle(10);
		expect(hold()).not.toBeNull();

		component!.$set({
			currentGeneration: { ...generating, status: 'failed' },
			isGenerating: false
		});
		await settle(10);

		expect(hold()).toBeNull();
	});

	it('clears the hold when the generation is cancelled', async () => {
		mountGenerating();
		await settle(10);

		component!.$set({ currentGeneration: null, isGenerating: false });
		await settle(10);

		expect(hold()).toBeNull();
	});

	it('clears the hold when a new generation starts', async () => {
		mountGenerating();
		await settle(10);

		component!.$set({
			currentGeneration: { id: 'gen-2', generation_id: 'gen-2', status: 'pending', file_type: 'video' },
			isGenerating: true
		});
		await settle(10);

		expect(hold()).toBeNull();
	});

	it('clears the hold when another generation is shown', async () => {
		mountGenerating();
		await settle(10);

		component!.$set({
			currentGeneration: { id: 'gen-9', generation_id: 'gen-9', status: 'completed', file_type: 'image', current_image: null },
			isGenerating: false
		});
		await settle(10);

		expect(hold()).toBeNull();
	});

	it('clears the hold when the generation completes without ever producing a video', async () => {
		vi.useFakeTimers();
		try {
			mountGenerating();
			await vi.advanceTimersByTimeAsync(20);
			component!.$set({ currentGeneration: { ...completed, current_image: PREVIEW }, isGenerating: false });
			await vi.advanceTimersByTimeAsync(20);
			expect(hold()).not.toBeNull();

			await vi.advanceTimersByTimeAsync(4000);

			expect(hold()).toBeNull();
		} finally {
			vi.useRealTimers();
		}
	});
});
