import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';

const getGenerationById = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationById: (...args: unknown[]) => getGenerationById(...args),
		getGenerationImageURL: vi.fn((id: string, name: string) => `/media/${id}/${name}`),
		getClient: vi.fn(() => ({ get: vi.fn(), post: vi.fn() }))
	}
}));

const { default: CompareGrid } = await import('$lib/generation/compare/view/CompareGrid.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { emptyCell } = await import('$lib/generation/compare/view/gridModel');

const cfg = {
	field: 'cfg',
	type: 'slider',
	label: 'CFG',
	values: [3, 4.5, 6].map((v) => ({ value: v, label: `CFG ${v}` }))
};

function videoGrid(statuses: Array<'completed' | 'running' | 'queued'>) {
	return {
		id: 'g-video',
		config: { armed: true, x: cfg, y: null, lockSeed: true },
		cols: 3,
		rows: 1,
		cells: statuses.map((status, i) => ({
			...emptyCell(i, 0),
			generationId: `vid-${i}`,
			status,
			mediaType: 'video',
			thumbnailUrl: `/poster-${i}.jpg`,
			axisValues: { cfg: String(cfg.values[i].value) }
		}))
	};
}

const mounted: Array<() => void> = [];

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({ component: CompareGrid as never, target, props });
	mounted.push(() => {
		instance.$destroy();
		target.remove();
	});
	return target;
}

const settle = async () => {
	for (let i = 0; i < 8; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

beforeEach(() => {
	getGenerationById.mockImplementation((id: string) =>
		Promise.resolve({
			success: true,
			data: { id, files: [{ file_path: `/out/${id}.mp4`, file_type: 'video', is_final: true }] }
		})
	);
	HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve());
	HTMLMediaElement.prototype.pause = vi.fn();
	vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1));
	vi.stubGlobal('cancelAnimationFrame', vi.fn());
});

afterEach(() => {
	while (mounted.length) mounted.pop()?.();
	vi.unstubAllGlobals();
	getGenerationById.mockReset();
});

describe('video compare grid', () => {
	it('plays every finished clip muted, looping-by-group and inline', async () => {
		const target = mount({ grid: videoGrid(['completed', 'completed', 'completed']) });
		await settle();
		const videos = [...target.querySelectorAll('video')] as HTMLVideoElement[];
		expect(videos).toHaveLength(3);
		for (const video of videos) {
			expect(video.muted).toBe(true);
			expect(video.hasAttribute('autoplay')).toBe(true);
			expect(video.hasAttribute('playsinline')).toBe(true);
		}
		expect(videos[1].getAttribute('src')).toBe('/media/vid-1/vid-1.mp4');
		expect(videos[0].getAttribute('poster')).toBe('/poster-0.jpg');
	});

	it('shows one transport under the grid with play, a seconds scrubber, mute and Synced', async () => {
		const target = mount({ grid: videoGrid(['completed', 'completed', 'completed']) });
		await settle();
		const transport = target.querySelector('[data-testid="video-transport"]') as HTMLElement;
		expect(transport).not.toBeNull();
		expect(target.querySelectorAll('[data-testid="video-transport"]')).toHaveLength(1);
		expect(transport.querySelector('button[aria-label="Pause all"]')).not.toBeNull();
		expect(transport.querySelector('input[type="range"]')).not.toBeNull();
		expect(transport.textContent).toContain('Unmute');
		expect(transport.querySelector('input[role="switch"][aria-label="Synced"]')).not.toBeNull();
		expect(transport.querySelector('[data-testid="video-transport-clock"]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
			'00:00.0 / 00:00.0'
		);
	});

	it('toggles play all and mute from the transport', async () => {
		const target = mount({ grid: videoGrid(['completed', 'completed', 'completed']) });
		await settle();
		const transport = target.querySelector('[data-testid="video-transport"]') as HTMLElement;
		(transport.querySelector('button[aria-label="Pause all"]') as HTMLButtonElement).click();
		await settle();
		expect(transport.querySelector('button[aria-label="Play all"]')).not.toBeNull();
		expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
		const muteButton = [...transport.querySelectorAll('button')].find((b) => b.textContent?.includes('Unmute')) as HTMLButtonElement;
		muteButton.click();
		await settle();
		expect(transport.textContent?.replace(/Unmute/g, '')).toContain('Mute');
		const videos = [...target.querySelectorAll('video')] as HTMLVideoElement[];
		expect(videos.every((v) => v.muted === false)).toBe(true);
	});

	it('turning Synced off lets each clip loop on its own', async () => {
		const target = mount({ grid: videoGrid(['completed', 'completed', 'completed']) });
		await settle();
		const videos = [...target.querySelectorAll('video')] as HTMLVideoElement[];
		expect(videos.every((v) => v.loop === false)).toBe(true);
		(target.querySelector('input[role="switch"][aria-label="Synced"]') as HTMLInputElement).click();
		await settle();
		expect(videos.every((v) => v.loop === true)).toBe(true);
	});

	it('has no transport until a clip has finished', async () => {
		const target = mount({ grid: videoGrid(['running', 'queued', 'queued']) });
		await settle();
		expect(target.querySelector('[data-testid="video-transport"]')).toBeNull();
		expect(target.querySelectorAll('video')).toHaveLength(0);
	});

	it('pauses a clip that scrolls out of view', async () => {
		const observers: Array<{ cb: IntersectionObserverCallback; el: Element }> = [];
		vi.stubGlobal(
			'IntersectionObserver',
			class {
				cb: IntersectionObserverCallback;
				constructor(cb: IntersectionObserverCallback) {
					this.cb = cb;
				}
				observe(el: Element) {
					observers.push({ cb: this.cb, el });
				}
				disconnect() {}
				unobserve() {}
			}
		);
		const target = mount({ grid: videoGrid(['completed', 'completed', 'completed']) });
		await settle();
		const first = [...target.querySelectorAll('video')][0] as HTMLVideoElement;
		const pause = vi.spyOn(first, 'pause');
		const entry = observers.find((o) => o.el === first) as { cb: IntersectionObserverCallback; el: Element };
		entry.cb([{ isIntersecting: false, target: first } as unknown as IntersectionObserverEntry], {} as IntersectionObserver);
		expect(pause).toHaveBeenCalled();
	});
});
