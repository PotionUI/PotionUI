import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test, expect, type Locator, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, scopedStorageKey, screenshot } from './helpers';

const JOURNEY = 'plugin-effect-invert';
const PLUGIN_NAME = 'E2E Effects';
const TOOL = 'e2e-effects.invert';
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
		multipart: { file: { name: 'invert-source.mp4', mimeType: 'video/mp4', buffer: CLIP }, purpose: 'user_upload' }
	});
	expect(response.ok(), `upload -> ${response.status()}`).toBeTruthy();
	return ((await response.json()) as { data: Upload }).data;
}

async function openInEditor(page: Page, upload: Upload, flag: string) {
	const key = await scopedStorageKey(page, 'potionui_tabs_state');
	const path = `uploads/${upload.filename}`;
	const source = { path, relative_path: path, url: upload.url, name: upload.original_filename || upload.filename, type: 'video', item_id: upload.id };
	await page.addInitScript(
		({ k, source, flag }) => {
			if (sessionStorage.getItem(flag)) return;
			sessionStorage.setItem(flag, '1');
			const saved = JSON.parse(localStorage.getItem(k) ?? 'null') ?? { tabs: [], activeTabId: '' };
			const tool = { id: 'video-tab-plugin', kind: 'tool', tool: 'video', name: 'invert-source', color: null, source, state: null, run: null, activeTool: 'effects', dirty: 0 };
			saved.tabs = [...saved.tabs.filter((tab: { kind?: string }) => tab.kind !== 'tool'), tool];
			saved.activeTabId = tool.id;
			localStorage.setItem(k, JSON.stringify(saved));
		},
		{ k: key, source, flag }
	);
	await page.goto('/');
	const editor = page.getByTestId('video-editor');
	await expect(editor).toHaveAttribute('data-ready', 'true', { timeout: 15000 });
	return editor;
}

async function pixelsOf(page: Page, png: string | null, url: string | null): Promise<number[]> {
	return page.evaluate(
		async ({ png, url }) => {
			let source: CanvasImageSource;
			let width: number;
			let height: number;
			if (png) {
				const image = new Image();
				image.src = `data:image/png;base64,${png}`;
				await image.decode();
				[source, width, height] = [image, image.naturalWidth, image.naturalHeight];
			} else {
				const video = url ? document.createElement('video') : document.querySelector<HTMLVideoElement>('[data-video-element]')!;
				if (url) {
					video.muted = true;
					video.src = url;
					await new Promise<void>((resolve, reject) => {
						video.onloadeddata = () => resolve();
						video.onerror = () => reject(new Error(`could not load ${url}`));
					});
					video.currentTime = 0.5;
					await new Promise<void>((resolve) => (video.onseeked = () => resolve()));
				}
				[source, width, height] = [video, video.videoWidth, video.videoHeight];
			}
			const scratch = document.createElement('canvas');
			scratch.width = width;
			scratch.height = height;
			const context = scratch.getContext('2d', { willReadFrequently: true })!;
			context.drawImage(source, 0, 0, width, height);
			const data = context.getImageData(0, 0, width, height).data;
			const out: number[] = [];
			for (let y = Math.floor(height * 0.1); y < height * 0.9; y += 7) {
				for (let x = Math.floor(width * 0.1); x < width * 0.9; x += 7) {
					const i = (y * width + x) * 4;
					out.push(data[i], data[i + 1], data[i + 2]);
				}
			}
			return out;
		},
		{ png, url }
	);
}

async function livePixels(page: Page, canvas: Locator): Promise<number[]> {
	const shot = await canvas.screenshot({ animations: 'disabled' });
	return pixelsOf(page, shot.toString('base64'), null);
}

function meanDiff(a: number[], b: number[]): number {
	const count = Math.min(a.length, b.length);
	let total = 0;
	for (let i = 0; i < count; i += 1) total += Math.abs(a[i] - b[i]);
	return count ? total / count : 255;
}

const negative = (values: number[]) => values.map((value) => 255 - value);

test('a plugin effect appears in the Video editor once the plugin is enabled, previews live and renders on Save', async ({ page }) => {
	test.setTimeout(150000);
	const errors: string[] = [];
	page.on('console', (message) => {
		if (message.type() === 'error') errors.push(message.text());
	});
	page.on('pageerror', (error) => errors.push(`${error.name}: ${error.message}`));

	await loginAsOwner(page);
	const token = await ownerToken(page);
	await page.request.post('/api/plugins/scan', { headers: { Authorization: `Bearer ${token}` } });
	const upload = await uploadClip(page);

	let editor = await openInEditor(page, upload, 'seeded-before');
	await expect(editor.locator('[data-effect-card="vignette"]')).toBeVisible({ timeout: 10000 });
	await expect(editor.locator(`[data-effect-card="${TOOL}"]`)).toHaveCount(0);

	await page.goto('/admin?tab=plugins');
	const catalog = page.locator('[role="list"][aria-label="Plugin catalog"]');
	await catalog.locator('[data-library-card]').filter({ hasText: PLUGIN_NAME }).first().click();
	await page.getByRole('switch', { name: 'Enable plugin' }).click();
	await expect(page.getByRole('switch', { name: 'Disable plugin' })).toBeVisible({ timeout: 20000 });
	await screenshot(page, JOURNEY, 'plugin-enabled');

	editor = await openInEditor(page, upload, 'seeded-after');
	const card = editor.locator(`[data-effect-card="${TOOL}"]`);
	await expect(card).toBeVisible({ timeout: 10000 });
	await expect(card).not.toContainText(/plugin|e2e-effects/i);
	await card.locator('button').first().click();
	await editor.locator('[data-options-apply]').click();
	await expect(editor.getByRole('button', { name: /Reopen Invert/ })).toBeVisible();

	const canvas = editor.locator('[data-live-preview]');
	await expect(canvas).toBeVisible({ timeout: 10000 });
	const source = await pixelsOf(page, null, null);
	await expect.poll(async () => meanDiff(await livePixels(page, canvas), negative(source)), { timeout: 15000 }).toBeLessThan(12);
	expect(meanDiff(await livePixels(page, canvas), source)).toBeGreaterThan(60);
	await screenshot(page, JOURNEY, 'invert-live');

	await editor.locator('[data-frame-save]').click();
	await expect(editor.locator('[data-frame-saving]')).toHaveCount(0, { timeout: 60000 });
	type Run = { status: string; items: Array<{ id: string; url: string }> };
	const finished = async (): Promise<Run[]> => {
		const body = (await (await page.request.get('/api/media-tools/runs', { headers: { Authorization: `Bearer ${token}` } })).json()) as { data: { runs: Run[] } };
		return body.data.runs;
	};
	await expect.poll(async () => (await finished()).some((run) => run.status === 'completed' && run.items.length > 0), { timeout: 60000 }).toBe(true);
	const done = (await finished()).find((run) => run.status === 'completed' && run.items.length > 0);
	const before = await pixelsOf(page, null, upload.url);
	const after = await pixelsOf(page, null, done!.items[0].url);
	expect(meanDiff(after, negative(before))).toBeLessThan(12);
	expect(meanDiff(after, before)).toBeGreaterThan(60);

	expect(errors).toEqual([]);
});
