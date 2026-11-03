import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test, expect, type Locator, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, scopedStorageKey, screenshot } from './helpers';

const JOURNEY = 'video-effects-look';
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

interface Colour {
	chroma: number;
	edge: number;
	middle: number;
}

async function uploadClip(page: Page): Promise<Upload> {
	const token = await ownerToken(page);
	const response = await page.request.post('/api/media/upload', {
		headers: { Authorization: `Bearer ${token}` },
		multipart: { file: { name: 'look-source.mp4', mimeType: 'video/mp4', buffer: CLIP }, purpose: 'user_upload' }
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
			const tool = { id: 'video-tab-look', kind: 'tool', tool: 'video', name: 'look-source', color: null, source, state: null, run: null, activeTool: 'effects', dirty: 0 };
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

async function colour(page: Page, png: string | null, url: string | null): Promise<Colour> {
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
			let chroma = 0;
			let edge = 0;
			let middle = 0;
			let edges = 0;
			let middles = 0;
			for (let i = 0; i < data.length; i += 4) {
				const pixel = i / 4;
				const x = pixel % width;
				const y = Math.floor(pixel / width);
				chroma += Math.max(data[i], data[i + 1], data[i + 2]) - Math.min(data[i], data[i + 1], data[i + 2]);
				const luma = (data[i] + data[i + 1] + data[i + 2]) / 3;
				const dx = Math.abs(x / width - 0.5);
				const dy = Math.abs(y / height - 0.5);
				if (dx > 0.42 && dy > 0.38) {
					edge += luma;
					edges += 1;
				} else if (dx < 0.15 && dy < 0.15) {
					middle += luma;
					middles += 1;
				}
			}
			return { chroma: chroma / (data.length / 4), edge: edge / Math.max(1, edges), middle: middle / Math.max(1, middles) };
		},
		{ png, url }
	);
}

async function live(page: Page, canvas: Locator): Promise<Colour> {
	const shot = await canvas.screenshot({ animations: 'disabled' });
	return colour(page, shot.toString('base64'), null);
}

const graded = (after: Colour, before: Colour) => before.chroma > 8 && after.chroma < 2.5;

test('a look from the Image editor grades the live video preview and the saved clip, with no mocks', async ({ page }) => {
	test.setTimeout(120000);
	const errors: string[] = [];
	page.on('console', (message) => {
		if (message.type() === 'error') errors.push(message.text());
	});
	page.on('pageerror', (error) => errors.push(`${error.name}: ${error.message}`));
	const tables: string[] = [];
	page.on('response', (response) => {
		if (new URL(response.url()).pathname === '/api/media-tools/looks/table') tables.push(`${response.status()}`);
	});

	await loginAsOwner(page);
	const upload = await uploadClip(page);
	const editor = await openInEditor(page, upload);

	await expect(editor.locator('[data-effect-later]')).toHaveCount(0);
	const card = editor.locator('[data-effect-card="look"]');
	await expect(card).toBeVisible({ timeout: 10000 });
	await card.locator('button').first().click();
	await card.locator('[data-param="look"]').getByRole('button', { name: 'Toggle options' }).click();
	await page.getByRole('option', { name: 'Noir' }).click();
	await editor.locator('[data-options-apply]').click();
	await expect(editor.locator('[data-stack-row]').filter({ hasText: 'Look' })).toBeVisible();

	const canvas = editor.locator('[data-live-preview]');
	await expect(canvas).toBeVisible({ timeout: 10000 });
	const source = await colour(page, null, null);
	await expect
		.poll(async () => JSON.stringify({ graded: graded(await live(page, canvas), source), live: await live(page, canvas), source }), { timeout: 20000 })
		.toContain('"graded":true');
	const shown = await live(page, canvas);
	expect(shown.edge / shown.middle, JSON.stringify({ shown, source })).toBeLessThan(source.edge / source.middle - 0.03);
	expect(tables).toEqual(['200']);
	await expect(editor.locator('[data-preview-clip]')).toHaveCount(0);
	await screenshot(page, JOURNEY, 'look-live-dark-1440');
	await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
	await screenshot(page, JOURNEY, 'look-live-light-1440');
	await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));

	const token = await ownerToken(page);
	type Run = { run_id: string; status: string; items: Array<{ id: string; url: string }> };
	const listRuns = async (): Promise<Run[]> =>
		((await (await page.request.get('/api/media-tools/runs', { headers: { Authorization: `Bearer ${token}` } })).json()) as { data: { runs: Run[] } }).data.runs;
	const seen = new Set((await listRuns()).map((run) => run.run_id));
	const fresh = (runs: Run[]) => runs.find((run) => !seen.has(run.run_id) && run.status === 'completed' && run.items.length > 0);
	await editor.locator('[data-frame-save]').click();
	await expect.poll(async () => !!fresh(await listRuns()), { timeout: 60000 }).toBe(true);
	const done = fresh(await listRuns())!;
	const before = await colour(page, null, upload.url);
	const after = await colour(page, null, done.items[0].url);
	expect(graded(after, before), JSON.stringify({ before, after })).toBe(true);
	expect(after.edge / after.middle, JSON.stringify({ before, after })).toBeLessThan(before.edge / before.middle - 0.03);

	expect(errors).toEqual([]);
});
