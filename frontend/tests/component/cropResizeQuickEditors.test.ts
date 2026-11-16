import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

class StubResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}
vi.stubGlobal('ResizeObserver', StubResizeObserver);

const startMediaToolRun = vi.fn();
const getMediaToolRun = vi.fn();
const fetchSpy = vi.fn(async () => new Response('{}'));
vi.stubGlobal('fetch', fetchSpy);

vi.mock('$lib/services/api/mediaTools', () => ({
	startMediaToolRun: (...args: unknown[]) => startMediaToolRun(...args),
	getMediaToolRun: (...args: unknown[]) => getMediaToolRun(...args),
	cancelMediaToolRun: vi.fn(),
	listMediaTools: vi.fn(),
	previewMediaTool: vi.fn()
}));

vi.mock('$lib/services/api/index', () => ({
	api: {
		listUploads: vi.fn(async () => ({ success: true, data: { uploads: [], total: 0, limit: 100, offset: 0 } })),
		listGenerationMedia: vi.fn(async () => ({ success: false })),
		copyGenerationFileToLibrary: vi.fn(async () => ({ success: false })),
		uploadMedia: vi.fn(),
		deleteLibraryItem: vi.fn(async () => ({ success: true }))
	}
}));

const { default: MediaEditors } = await import('$lib/media/editors/MediaEditors.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');
const { mediaToolRuns } = await import('$lib/media-tools/runs');

const image = {
	url: '/api/media/uploads/photo.png',
	kind: 'image' as const,
	fileName: 'photo.png',
	itemId: 'row-1',
	storedPath: 'uploads/photo.png',
	width: 3000,
	height: 2000
};

const clip = { ...image, url: '/api/media/uploads/clip.mp4', kind: 'video' as const, fileName: 'clip.mp4', durationSeconds: 8, fps: 24 };

async function settle() {
	for (let i = 0; i < 5; i += 1) {
		await tick();
		await new Promise((resolve) => setTimeout(resolve, 0));
	}
}

async function open(kind: 'crop' | 'resize', source: typeof image | typeof clip) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({
		component: MediaEditors as never,
		target,
		props: { request: { kind, source, itemIndex: null }, scope: 'field', onClose: vi.fn(), onResult: vi.fn() }
	});
	await settle();
}

const buttons = () => Array.from(document.body.querySelectorAll('button'));
const byText = (text: string) => buttons().find((button) => (button.textContent || '').replace(/\s+/g, ' ').trim() === text);
const preset = (size: string) => buttons().find((button) => (button.textContent || '').includes(size) && button.hasAttribute('aria-pressed') && button.className.includes('h-12'));
const byLabel = (label: string) => document.body.querySelector<HTMLElement>(`[aria-label="${label}"]`);

beforeEach(() => {
	document.body.innerHTML = '';
	startMediaToolRun.mockReset().mockResolvedValue({ run_id: 'run-1' });
	getMediaToolRun.mockReset().mockResolvedValue({
		run_id: 'run-1',
		status: 'completed',
		progress: 1,
		label: '',
		items: [{ id: 'row-9', filename: 'out.png', media_type: 'image', url: '/api/media/uploads/out.png' }],
		replaced: false,
		error: null
	});
	fetchSpy.mockClear();
});

afterEach(() => {
	mediaToolRuns.destroy();
});

describe('crop modal for pictures', () => {
	it('keeps every aspect, orientation, size and reset control the old crop modal had', async () => {
		await open('crop', image);
		for (const aspect of ['FREE', '1:1', '16:9', '9:16', '4:3', '3:4']) expect(byText(aspect), aspect).toBeDefined();
		for (const label of ['Rotate left', 'Rotate right', 'Flip horizontal', 'Flip vertical']) expect(byLabel(label), label).not.toBeNull();
		for (const side of ['FULL', '2048', '1536', '1280', '1024', '768', '512']) expect(byText(side), side).toBeDefined();
		expect(byText('Reset')).toBeDefined();
		expect(byLabel('Crop X')).not.toBeNull();
		expect(document.body.querySelector('[data-crop-output]')!.textContent).toContain('3000×2000');
		expect(document.body.querySelector('[data-crop-corner]')).not.toBeNull();
	});

	it('crops to a ratio, picks a longest side and sends one operations run without any edits request', async () => {
		await open('crop', image);
		byText('1:1')!.click();
		await settle();
		byText('1024')!.click();
		await settle();
		byText('Apply')!.click();
		await settle();

		expect(startMediaToolRun).toHaveBeenCalledTimes(1);
		const body = startMediaToolRun.mock.calls[0][0];
		expect(body.inputs).toEqual([{ item_id: 'row-1' }]);
		expect(body.output).toEqual({ mode: 'new' });
		expect(body.steps.length).toBeGreaterThan(0);
		const urls = fetchSpy.mock.calls.map((call) => String((call as unknown[])[0]));
		expect(urls.filter((url) => url.includes('/api/edits'))).toEqual([]);
	});

	it('resets the rectangle and the aspect', async () => {
		await open('crop', image);
		byText('1:1')!.click();
		await settle();
		expect(byText('1:1')!.getAttribute('aria-pressed')).toBe('true');
		byText('Reset')!.click();
		await settle();
		expect(byText('FREE')!.getAttribute('aria-pressed')).toBe('true');
	});

	it('leaves clips on the video crop modal', async () => {
		await open('crop', clip);
		expect(document.body.querySelector('video')).not.toBeNull();
		expect(document.body.querySelector('[data-crop-stage]')).toBeNull();
	});
});

describe('resize modal for pictures', () => {
	it('offers source based sizes, a custom size that follows the shape and no upscaling shortcuts', async () => {
		await open('resize', image);
		expect(document.body.textContent).toContain('Common sizes');
		expect(document.body.querySelector('[aria-label="Scale"]')).toBeNull();
		expect(buttons().filter((button) => (button.textContent || '').startsWith('50%')).length).toBe(1);
		expect(document.body.textContent).not.toContain('200%');
		expect(byLabel('Keep aspect ratio')!.getAttribute('aria-pressed')).toBe('true');

		const width = document.body.querySelector<HTMLInputElement>('[data-resize-width]')!;
		width.value = '1000';
		width.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();
		expect(document.body.querySelector<HTMLInputElement>('[data-resize-height]')!.value).toBe('667');
	});

	it('refuses the original size and a size larger than the original', async () => {
		await open('resize', image);
		preset('3000×2000')!.click();
		await settle();
		expect(document.body.textContent).toContain('Pick a size smaller than the original.');
		expect(byText('Apply')!.disabled).toBe(true);

		const width = document.body.querySelector<HTMLInputElement>('[data-resize-width]')!;
		width.value = '4000';
		width.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();
		expect(byText('Apply')!.disabled).toBe(true);
		expect(startMediaToolRun).not.toHaveBeenCalled();
	});

	it('sends the chosen size as one resize step and creates no edit', async () => {
		await open('resize', image);
		preset('2250×1500')!.click();
		await settle();
		byText('Apply')!.click();
		await settle();

		expect(startMediaToolRun).toHaveBeenCalledWith({
			steps: [{ tool: 'resize', params: { width: 2250, height: 1500 } }],
			inputs: [{ item_id: 'row-1' }],
			output: { mode: 'new' }
		});
		const urls = fetchSpy.mock.calls.map((call) => String((call as unknown[])[0]));
		expect(urls.filter((url) => url.includes('/api/edits'))).toEqual([]);
	});

	it('leaves clips on the video resize modal with its quality choice', async () => {
		await open('resize', clip);
		expect(byText('Small file')).toBeDefined();
	});
});
