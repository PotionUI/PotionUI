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

vi.mock('$lib/components/AmbientGlow.svelte', async () => ({
	default: (await import('./fixtures/AmbientGlowProbe.svelte')).default
}));

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

const generating = { id: 'gen-1', generation_id: 'gen-1', status: 'running', file_type: 'image', current_image: PREVIEW };
const probes = () => Array.from(target.querySelectorAll<HTMLElement>('[data-ambient-probe]'));

describe('Workbench backdrop while a video result lands', () => {
	it('keeps the preview as the backdrop until the player has a frame and then crossfades to the video', async () => {
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
		await settle(10);

		component!.$set({
			currentGeneration: { ...generating, status: 'completed', current_image: null },
			isGenerating: false,
			workbenchTotal: 1,
			batchVideos: [{ url: VIDEO_URL, originalUrl: VIDEO_URL, file_type: 'video' }]
		});
		await settle(10);

		expect(probes().length).toBeGreaterThan(0);
		for (const probe of probes()) {
			expect(probe.dataset.kind).toBe('image');
			expect(probe.dataset.src).toBe(PREVIEW);
		}

		target.querySelector('[data-video-stage] video')!.dispatchEvent(new Event('loadeddata'));
		await settle(10);

		for (const probe of probes()) {
			expect(probe.dataset.kind).toBe('video');
			expect(Number(probe.dataset.crossfade)).toBeGreaterThan(0);
		}
	});
});
