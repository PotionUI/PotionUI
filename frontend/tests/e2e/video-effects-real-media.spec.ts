import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, scopedStorageKey, screenshot } from './helpers';

const JOURNEY = 'video-effects-real-media';
const CLIP = readFileSync(fileURLToPath(new URL('./fixtures/grain-source.mp4', import.meta.url)));
const { DISPLAY: _display, WAYLAND_DISPLAY: _wayland, ...browserEnv } = process.env;

test.use({
	viewport: { width: 1440, height: 900 },
	launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'], env: browserEnv as Record<string, string> }
});

interface Upload {
	id: string;
	filename: string;
	original_filename?: string;
	url: string;
}

async function uploadClip(page: Page): Promise<Upload> {
	const token = await ownerToken(page);
	const response = await page.request.post('/api/media/upload', {
		headers: { Authorization: `Bearer ${token}` },
		multipart: { file: { name: 'grain-source.mp4', mimeType: 'video/mp4', buffer: CLIP }, purpose: 'user_upload' }
	});
	expect(response.ok(), `upload -> ${response.status()}`).toBeTruthy();
	const body = (await response.json()) as { data: Upload };
	return body.data;
}

async function openInEditor(page: Page, upload: Upload) {
	const key = await scopedStorageKey(page, 'potionui_tabs_state');
	const path = `uploads/${upload.filename}`;
	const source = { path, relative_path: path, url: upload.url, name: upload.original_filename || upload.filename, type: 'video', item_id: upload.id };
	await page.addInitScript(
		({ k, source }) => {
			if (sessionStorage.getItem('e2e-video-seeded')) return;
			sessionStorage.setItem('e2e-video-seeded', '1');
			const saved = JSON.parse(localStorage.getItem(k) ?? 'null') ?? { tabs: [], activeTabId: '' };
			const generation = saved.tabs.find((tab: { kind?: string }) => tab.kind !== 'tool');
			const tool = { id: 'video-tab-real', kind: 'tool', tool: 'video', name: 'grain-source', color: null, source, state: null, run: null, activeTool: 'effects', dirty: 0 };
			saved.tabs = [...saved.tabs.filter((tab: { kind?: string }) => tab.kind !== 'tool'), tool];
			saved.activeTabId = tool.id;
			if (generation) saved.lastGenerationTabId = generation.id;
			localStorage.setItem(k, JSON.stringify(saved));
		},
		{ k: key, source }
	);
	await page.reload();
	const editor = page.getByTestId('video-editor');
	await expect(editor).toHaveAttribute('data-ready', 'true', { timeout: 15000 });
	return editor;
}

interface Sample {
	canvas: number[];
	video: number[];
}

async function sample(page: Page): Promise<Sample> {
	const shot = await page.locator('[data-live-preview]').screenshot({ animations: 'disabled' });
	return page.evaluate(async (png) => {
		const image = new Image();
		image.src = `data:image/png;base64,${png}`;
		await image.decode();
		const video = document.querySelector<HTMLVideoElement>('[data-video-element]')!;
		const width = video.videoWidth;
		const height = video.videoHeight;
		const read = (source: CanvasImageSource) => {
			const scratch = document.createElement('canvas');
			scratch.width = width;
			scratch.height = height;
			const context = scratch.getContext('2d', { willReadFrequently: true })!;
			context.imageSmoothingEnabled = false;
			context.drawImage(source, 0, 0, width, height);
			const data = context.getImageData(0, 0, width, height).data;
			const out: number[] = [];
			for (let y = Math.round(height * 0.45); y < height - 2; y += 5) for (let x = 2; x < width; x += 5) out.push(data[(y * width + x) * 4], data[(y * width + x) * 4 + 1], data[(y * width + x) * 4 + 2]);
			return out;
		};
		return { canvas: read(image), video: read(video) };
	}, shot.toString('base64'));
}

async function frameAt(page: Page, url: string, seconds: number): Promise<number[]> {
	return page.evaluate(
		async ({ url, seconds }) => {
			const video = document.createElement('video');
			video.muted = true;
			video.preload = 'auto';
			video.src = url;
			await new Promise<void>((resolve, reject) => {
				video.onloadeddata = () => resolve();
				video.onerror = () => reject(new Error(`could not load ${url}`));
			});
			video.currentTime = seconds;
			await new Promise<void>((resolve) => (video.onseeked = () => resolve()));
			const scratch = document.createElement('canvas');
			scratch.width = video.videoWidth;
			scratch.height = video.videoHeight;
			const context = scratch.getContext('2d', { willReadFrequently: true })!;
			context.drawImage(video, 0, 0);
			const data = context.getImageData(0, 0, scratch.width, scratch.height).data;
			const out: number[] = [];
			for (let i = 0; i < data.length; i += 4 * 37) out.push(data[i], data[i + 1], data[i + 2]);
			return out;
		},
		{ url, seconds }
	);
}

function meanDiff(a: number[], b: number[]): number {
	let total = 0;
	const count = Math.min(a.length, b.length);
	for (let i = 0; i < count; i += 1) total += Math.abs(a[i] - b[i]);
	return count ? total / count : 0;
}

const brightness = (values: number[]) => values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);

test('grain previews live on a real uploaded clip with no mocks, and Save renders a grainy Library item', async ({ page }) => {
	test.setTimeout(120000);
	const errors: string[] = [];
	page.on('console', (message) => {
		if (message.type() === 'error') errors.push(message.text());
	});
	page.on('pageerror', (error) => errors.push(`${error.name}: ${error.message}`));

	await loginAsOwner(page);
	const upload = await uploadClip(page);
	const editor = await openInEditor(page, upload);

	const card = editor.locator('[data-effect-card="film_grain"]');
	await expect(card).toBeVisible({ timeout: 10000 });
	await card.locator('button').first().click();
	await card.locator('[data-param="amount"] input[type="range"]').fill('100');
	await editor.locator('[data-options-apply]').click();
	await expect(editor.locator('[data-stack-row]').filter({ hasText: 'Film grain' })).toBeVisible();

	const canvas = editor.locator('[data-live-preview]');
	await expect(canvas).toBeVisible({ timeout: 10000 });
	await expect.poll(async () => brightness((await sample(page)).canvas), { timeout: 15000 }).toBeGreaterThan(20);
	await expect.poll(async () => {
		const still = await sample(page);
		return meanDiff(still.canvas, still.video);
	}, { timeout: 10000 }).toBeGreaterThan(4);
	await expect(editor.locator('[data-preview-clip]')).toHaveCount(0);
	await screenshot(page, JOURNEY, 'real-grain-1440');

	await editor.locator('[data-scrub-play]').click();
	await expect.poll(() => editor.locator('[data-video-element]').evaluate((el) => !(el as HTMLVideoElement).paused)).toBe(true);
	const first = (await sample(page)).canvas;
	await expect.poll(async () => meanDiff((await sample(page)).canvas, first), { timeout: 5000 }).toBeGreaterThan(1);
	await editor.locator('[data-scrub-play]').click();

	const token = await ownerToken(page);
	await editor.locator('[data-frame-save]').click();
	await expect(editor.locator('[data-frame-saving]')).toHaveCount(0, { timeout: 60000 });
	await expect(editor.locator('[data-frame-save]')).toHaveText(/^\s*Save\s*$/, { timeout: 10000 });
	const runs = (await (await page.request.get('/api/media-tools/runs', { headers: { Authorization: `Bearer ${token}` } })).json()) as {
		data: { runs: Array<{ status: string; items: Array<{ id: string; url: string }> }> };
	};
	const done = runs.data.runs.find((run) => run.status === 'completed' && run.items.length > 0);
	expect(done, JSON.stringify(runs.data.runs)).toBeTruthy();
	const output = done!.items[0];
	expect(output.id).not.toBe(upload.id);
	const before = await frameAt(page, upload.url, 0.5);
	const after = await frameAt(page, output.url, 0.5);
	expect(brightness(after)).toBeGreaterThan(20);
	expect(meanDiff(after, before)).toBeGreaterThan(4);

	expect(errors.filter((text) => /security|webgl|teximage|cross-origin|tainted|shader/i.test(text))).toEqual([]);
	test.info().annotations.push({ type: 'console-errors', description: JSON.stringify(errors) });
	expect(errors).toEqual([]);
});
