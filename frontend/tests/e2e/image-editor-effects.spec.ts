import { deflateSync } from 'node:zlib';
import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { chunk } from './mediaFieldHelpers';

const JOURNEY = 'image-editor-effects';
const WIDTH = 640;
const HEIGHT = 400;
const OPAQUE = 512;
const { DISPLAY: _display, WAYLAND_DISPLAY: _wayland, ...browserEnv } = process.env;

test.use({
	viewport: { width: 1440, height: 900 },
	launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'], env: browserEnv as Record<string, string> }
});

function cutoutPng(): Buffer {
	const rows: Buffer[] = [];
	for (let y = 0; y < HEIGHT; y++) {
		const row = Buffer.alloc(1 + WIDTH * 4);
		for (let x = 0; x < WIDTH; x++) {
			const pixel = x < OPAQUE ? [200, 150 + Math.round((y * 60) / HEIGHT), 110, 255] : [40, 200, 90, 0];
			row.set(pixel, 1 + x * 4);
		}
		rows.push(row);
	}
	const header = Buffer.alloc(13);
	header.writeUInt32BE(WIDTH, 0);
	header.writeUInt32BE(HEIGHT, 4);
	header[8] = 8;
	header[9] = 6;
	return Buffer.concat([
		Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
		chunk('IHDR', header),
		chunk('IDAT', deflateSync(Buffer.concat(rows))),
		chunk('IEND', Buffer.alloc(0))
	]);
}

type Probe = { corner: number[]; middle: number[]; cutout: number[]; width: number; height: number; ms?: number[] };

const PROBE = { corner: [0.01, 0.015], middle: [0.4, 0.5], cutout: [0.95, 0.5] } as const;

function probeCanvas(page: Page): Promise<Probe> {
	return page.locator('[data-editor-layer]').evaluate((node, points) => {
		const canvas = node as HTMLCanvasElement;
		const context = canvas.getContext('2d')!;
		const ms: number[] = [];
		const at = ([fx, fy]: readonly number[]) => {
			const began = performance.now();
			const data = Array.from(context.getImageData(Math.floor(canvas.width * fx), Math.floor(canvas.height * fy), 1, 1).data);
			ms.push(Math.round(performance.now() - began));
			return data;
		};
		return { corner: at(points.corner), middle: at(points.middle), cutout: at(points.cutout), width: canvas.width, height: canvas.height, ms };
	}, PROBE);
}

function probeUrl(page: Page, url: string): Promise<Probe> {
	return page.evaluate(
		async ({ url, points }) => {
			const image = new Image();
			image.src = url;
			await image.decode();
			const canvas = document.createElement('canvas');
			canvas.width = image.naturalWidth;
			canvas.height = image.naturalHeight;
			const context = canvas.getContext('2d', { willReadFrequently: true })!;
			context.drawImage(image, 0, 0);
			const at = ([fx, fy]: readonly number[]) => Array.from(context.getImageData(Math.floor(canvas.width * fx), Math.floor(canvas.height * fy), 1, 1).data);
			return { corner: at(points.corner), middle: at(points.middle), cutout: at(points.cutout), width: canvas.width, height: canvas.height };
		},
		{ url, points: PROBE }
	);
}

const brightness = (pixel: number[]) => pixel[0] + pixel[1] + pixel[2];

test('Vignette from the effect catalog previews live on the image canvas through the shader and saves through a media-tools run, with no mocks', async ({ page }) => {
	test.setTimeout(150000);
	const errors: string[] = [];
	page.on('console', (message) => {
		if (message.type() === 'error') errors.push(message.text());
	});
	page.on('pageerror', (error) => errors.push(`${error.name}: ${error.message}`));
	const runBodies: Array<{ steps: Array<{ tool: string; params: Record<string, unknown> }> }> = [];
	const uploads: string[] = [];
	page.on('request', (request) => {
		const path = new URL(request.url()).pathname;
		if (request.method() === 'POST' && path === '/api/media-tools/runs') runBodies.push(request.postDataJSON());
		if (request.method() === 'POST' && path.startsWith('/api/media-tools/artifacts')) uploads.push(path);
	});

	await loginAsOwner(page);
	const token = await ownerToken(page);
	const upload = await page.request.post('/api/media/upload', {
		headers: { Authorization: `Bearer ${token}` },
		multipart: { file: { name: `effects-${Date.now()}.png`, mimeType: 'image/png', buffer: cutoutPng() } }
	});
	expect(upload.ok(), `upload -> ${upload.status()}`).toBeTruthy();
	const source = ((await upload.json()) as { data: { url: string } }).data;

	await page.goto('/generate');
	await expect(page.locator('.tab-bar-container')).toBeVisible({ timeout: 15000 });
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
	const empty = page.getByTestId('tool-tab-empty');
	await empty.getByRole('button', { name: 'Library', exact: true }).click();
	await page.getByRole('button', { name: /^Use / }).first().click();
	await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop', { timeout: 15000 });

	let original: Probe | null = null;
	const samples: string[] = [];
	const opened = Date.now();
	try {
		await expect
			.poll(
				async () => {
					const began = Date.now() - opened;
					samples.push(`start +${began}ms`);
					original = await probeCanvas(page);
					samples.push(`end +${Date.now() - opened}ms ${JSON.stringify(original)}`);
					return original.middle[3] === 255 && original.cutout[3] === 0;
				},
				{ timeout: 90000 }
			)
			.toBe(true);
	} catch (error) {
		throw new Error(`${(error as Error).message}\nprobe samples:\n${samples.join('\n')}\nerrors: ${JSON.stringify(errors)}`);
	}

	await page.locator('[data-tool-rail] button[data-tool="effects"]').click();
	const card = page.locator('[data-effect-card="vignette"]');
	await expect(card).toBeVisible();
	await card.click();
	await expect(card).toHaveAttribute('data-open', 'true');
	await expect(page.locator('[data-param="amount"]')).toBeVisible();

	await expect
		.poll(async () => {
			const now = await probeCanvas(page);
			return JSON.stringify({ darker: brightness(now.corner) < brightness(original!.corner) - 40, kept: Math.abs(brightness(now.middle) - brightness(original!.middle)) <= 6, clear: now.cutout[3] === 0, now });
		}, { timeout: 20000 })
		.toContain('"darker":true,"kept":true,"clear":true');
	await expect(page.locator('[data-render-error]')).toHaveCount(0);
	await screenshot(page, JOURNEY, 'vignette-live-dark-1440');
	await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
	await screenshot(page, JOURNEY, 'vignette-live-light-1440');
	await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));

	await page.locator('[data-options-apply]').click();
	await expect(page.locator('[data-edit-stack="panel"] [data-stack-row]')).toHaveCount(2);

	type Run = { run_id: string; status: string; items: Array<{ id: string; url: string }> };
	const listRuns = async (): Promise<Run[]> =>
		((await (await page.request.get('/api/media-tools/runs', { headers: { Authorization: `Bearer ${token}` } })).json()) as { data: { runs: Run[] } }).data.runs;
	const seen = new Set((await listRuns()).map((run) => run.run_id));
	const fresh = (runs: Run[]) => runs.find((run) => !seen.has(run.run_id) && run.status === 'completed' && run.items.length > 0);
	await page.locator('[data-frame-save]').click();
	await expect.poll(async () => !!fresh(await listRuns()), { timeout: 60000 }).toBe(true);

	expect(runBodies.at(-1)?.steps.map((step) => step.tool)).toEqual(['vignette']);
	expect(runBodies.at(-1)?.steps[0].params).toMatchObject({ amount: 40, radius: 50, softness: 50 });
	expect(uploads).toEqual([]);
	const saved = await probeUrl(page, fresh(await listRuns())!.items[0].url);
	const before = await probeUrl(page, source.url);
	expect([saved.width, saved.height]).toEqual([WIDTH, HEIGHT]);
	expect(brightness(saved.corner), JSON.stringify({ saved, before })).toBeLessThan(brightness(before.corner) - 40);
	expect(Math.abs(brightness(saved.middle) - brightness(before.middle)), JSON.stringify({ saved, before })).toBeLessThanOrEqual(6);
	expect(saved.cutout[3]).toBe(0);
	await screenshot(page, JOURNEY, 'vignette-saved-1440');

	expect(errors).toEqual([]);
});
