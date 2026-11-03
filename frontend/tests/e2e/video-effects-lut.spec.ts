import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test, expect, type Locator, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, scopedStorageKey, screenshot } from './helpers';

const JOURNEY = 'video-effects-lut';
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
		multipart: { file: { name: 'lut-source.mp4', mimeType: 'video/mp4', buffer: CLIP }, purpose: 'user_upload' }
	});
	expect(response.ok(), `upload -> ${response.status()}`).toBeTruthy();
	return ((await response.json()) as { data: Upload }).data;
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
			const tool = { id: 'video-tab-lut', kind: 'tool', tool: 'video', name: 'lut-source', color: null, source, state: null, run: null, activeTool: 'effects', dirty: 0 };
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

async function channelMeans(page: Page, png: string | null, url: string | null, seconds = 0.5): Promise<number[]> {
	return page.evaluate(
		async ({ png, url, seconds }) => {
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
					video.currentTime = seconds;
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
			const sums = [0, 0, 0];
			for (let i = 0; i < data.length; i += 4) for (let c = 0; c < 3; c++) sums[c] += data[i + c];
			return sums.map((sum) => sum / (data.length / 4));
		},
		{ png, url, seconds }
	);
}

async function liveMeans(page: Page, canvas: Locator): Promise<number[]> {
	const shot = await canvas.screenshot({ animations: 'disabled' });
	return channelMeans(page, shot.toString('base64'), null);
}

const warmer = (graded: number[], source: number[]) => source[2] - graded[2] > 4 && graded[0] >= source[0] - 1;

test('a LUT from the LUT folder grades the live preview and the saved clip, with no mocks', async ({ page }) => {
	test.setTimeout(150000);
	const errors: string[] = [];
	page.on('console', (message) => {
		if (message.type() === 'error') errors.push(message.text());
	});
	page.on('pageerror', (error) => errors.push(`${error.name}: ${error.message}`));

	await loginAsOwner(page);
	const upload = await uploadClip(page);
	const editor = await openInEditor(page, upload);

	const card = editor.locator('[data-effect-card="lut"]');
	await expect(card).toBeVisible({ timeout: 10000 });
	await card.locator('button').first().click();
	await card.locator('[data-param="lut"] button[aria-haspopup="listbox"]').click();
	await page.getByRole('option', { name: 'e2e-warm' }).click();
	await editor.locator('[data-options-apply]').click();
	await expect(editor.getByRole('button', { name: /Reopen LUT/ })).toBeVisible();

	const canvas = editor.locator('[data-live-preview]');
	await expect(canvas).toBeVisible({ timeout: 10000 });
	const source = await channelMeans(page, null, null);
	await expect.poll(async () => JSON.stringify({ warmer: warmer(await liveMeans(page, canvas), source), live: await liveMeans(page, canvas), source }), { timeout: 15000 }).toContain('"warmer":true');
	await expect(editor.locator('[data-preview-clip]')).toHaveCount(0);
	await screenshot(page, JOURNEY, 'lut-live-1440');

	const token = await ownerToken(page);
	type Run = { run_id: string; status: string; items: Array<{ id: string; url: string }> };
	const listRuns = async (): Promise<Run[]> =>
		((await (await page.request.get('/api/media-tools/runs', { headers: { Authorization: `Bearer ${token}` } })).json()) as { data: { runs: Run[] } }).data.runs;
	const seen = new Set((await listRuns()).map((run) => run.run_id));
	const fresh = (runs: Run[]) => runs.find((run) => !seen.has(run.run_id) && run.status === 'completed' && run.items.length > 0);
	await editor.locator('[data-frame-save]').click();
	await expect.poll(async () => !!fresh(await listRuns()), { timeout: 60000 }).toBe(true);
	const done = fresh(await listRuns())!;
	const before = await channelMeans(page, null, upload.url);
	const after = await channelMeans(page, null, done.items[0].url);
	expect(warmer(after, before), JSON.stringify({ before, after })).toBe(true);

	expect(errors).toEqual([]);
});
