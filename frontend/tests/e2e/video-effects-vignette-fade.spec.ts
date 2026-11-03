import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test, expect, type Locator, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, scopedStorageKey, screenshot } from './helpers';

const JOURNEY = 'video-effects-vignette-fade';
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

interface Regions {
	centre: number;
	corners: number;
}

async function uploadClip(page: Page): Promise<Upload> {
	const token = await ownerToken(page);
	const response = await page.request.post('/api/media/upload', {
		headers: { Authorization: `Bearer ${token}` },
		multipart: { file: { name: 'fade-source.mp4', mimeType: 'video/mp4', buffer: CLIP }, purpose: 'user_upload' }
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
			const tool = { id: 'video-tab-vfx', kind: 'tool', tool: 'video', name: 'fade-source', color: null, source, state: null, run: null, activeTool: 'effects', dirty: 0 };
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

async function regionsOf(page: Page, png: string | null, url: string | null, seconds: number): Promise<Regions> {
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
					video.preload = 'auto';
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
			context.imageSmoothingEnabled = false;
			context.drawImage(source, 0, 0, width, height);
			const data = context.getImageData(0, 0, width, height).data;
			const mean = (x0: number, y0: number, w: number, h: number) => {
				let total = 0;
				let count = 0;
				for (let y = Math.floor(y0); y < Math.floor(y0 + h); y++) {
					for (let x = Math.floor(x0); x < Math.floor(x0 + w); x++) {
						const i = (y * width + x) * 4;
						total += (data[i] + data[i + 1] + data[i + 2]) / 3;
						count += 1;
					}
				}
				return count ? total / count : 0;
			};
			const w = width * 0.06;
			const h = height * 0.08;
			const corners = (mean(1, 1, w, h) + mean(width - w - 1, 1, w, h) + mean(1, height - h - 1, w, h) + mean(width - w - 1, height - h - 1, w, h)) / 4;
			return { centre: mean(width * 0.4, height * 0.4, width * 0.2, height * 0.2), corners };
		},
		{ png, url, seconds }
	);
}

async function liveRegions(page: Page, canvas: Locator): Promise<Regions> {
	const shot = await canvas.screenshot({ animations: 'disabled' });
	return regionsOf(page, shot.toString('base64'), null, 0);
}

async function addEffect(editor: Locator, id: string, values: Record<string, string>) {
	const card = editor.locator(`[data-effect-card="${id}"]`);
	await expect(card).toBeVisible({ timeout: 10000 });
	await card.locator('button').first().click();
	await expect(card).toHaveAttribute('data-open', 'true');
	for (const [name, value] of Object.entries(values)) await card.locator(`[data-param="${name}"] input[type="range"]`).fill(value);
	await editor.locator('[data-options-apply]').click();
}

test('vignette and fade preview live on a real clip with no mocks, and Save renders both into the Library item', async ({ page }) => {
	test.setTimeout(150000);
	const errors: string[] = [];
	page.on('console', (message) => {
		if (message.type() === 'error') errors.push(message.text());
	});
	page.on('pageerror', (error) => errors.push(`${error.name}: ${error.message}`));

	await loginAsOwner(page);
	const upload = await uploadClip(page);
	const editor = await openInEditor(page, upload);
	const canvas = editor.locator('[data-live-preview]');

	await addEffect(editor, 'vignette', { amount: '100' });
	await expect(editor.locator('[data-stack-row]').filter({ hasText: 'Vignette' })).toBeVisible();
	await expect(canvas).toBeVisible({ timeout: 10000 });
	const source = await regionsOf(page, null, null, 0);
	expect(source.corners).toBeGreaterThan(source.centre * 0.8);
	await expect.poll(async () => {
		const live = await liveRegions(page, canvas);
		return live.centre > 60 && live.corners < live.centre * 0.4;
	}, { timeout: 15000 }).toBe(true);
	await screenshot(page, JOURNEY, 'vignette-live-1440');

	await addEffect(editor, 'fade', { fade_in: '0.5', fade_out: '0.5' });
	await expect(editor.locator('[data-stack-row]').filter({ hasText: 'Fade' })).toBeVisible();
	await expect.poll(() => editor.locator('[data-video-element]').evaluate((el) => (el as HTMLVideoElement).currentTime), { timeout: 10000 }).toBeLessThan(0.05);
	await expect.poll(async () => (await liveRegions(page, canvas)).centre, { timeout: 15000 }).toBeLessThan(8);
	await expect(editor.locator('[data-preview-clip]')).toHaveCount(0);
	await screenshot(page, JOURNEY, 'fade-live-first-frame-1440');

	const token = await ownerToken(page);
	type Run = { run_id: string; status: string; items: Array<{ id: string; url: string }> };
	const listRuns = async (): Promise<Run[]> =>
		((await (await page.request.get('/api/media-tools/runs', { headers: { Authorization: `Bearer ${token}` } })).json()) as { data: { runs: Run[] } }).data.runs;
	const seen = new Set((await listRuns()).map((run) => run.run_id));
	const fresh = (runs: Run[]) => runs.find((run) => !seen.has(run.run_id) && run.status === 'completed' && run.items.length > 0);
	await editor.locator('[data-frame-save]').click();
	await expect.poll(async () => !!fresh(await listRuns()), { timeout: 60000 }).toBe(true);
	const done = fresh(await listRuns())!;
	const output = done!.items[0];
	expect(output.id).not.toBe(upload.id);

	const first = await regionsOf(page, null, output.url, 0);
	expect(first.centre).toBeLessThan(8);
	const middle = await regionsOf(page, null, output.url, 1);
	const before = await regionsOf(page, null, upload.url, 1);
	expect(middle.centre).toBeGreaterThan(before.centre - 12);
	expect(middle.corners).toBeLessThan(middle.centre * 0.4);
	expect(before.corners).toBeGreaterThan(before.centre * 0.8);

	expect(errors.filter((text) => /security|webgl|teximage|cross-origin|tainted|shader/i.test(text))).toEqual([]);
	test.info().annotations.push({ type: 'console-errors', description: JSON.stringify(errors) });
	expect(errors).toEqual([]);
});
