import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { solidPng } from './mediaToolMocks';

const JOURNEY = 'image-editor-real-backend';

interface Trace {
	console: string[];
	failed: string[];
}

interface Pixels {
	width: number;
	height: number;
	samples: number[][];
	error?: string;
}

function watch(page: Page): Trace {
	const trace: Trace = { console: [], failed: [] };
	page.on('console', (message) => {
		if (message.type() === 'error') trace.console.push(message.text());
	});
	page.on('pageerror', (error) => trace.console.push(`pageerror: ${error.message}`));
	page.on('requestfailed', (request) => trace.failed.push(`${request.method()} ${new URL(request.url()).pathname} ${request.failure()?.errorText ?? ''}`));
	page.on('response', (response) => {
		if (response.status() >= 400) trace.failed.push(`${response.request().method()} ${new URL(response.url()).pathname} ${response.status()}`);
	});
	return trace;
}

function canvasPixels(page: Page): Promise<Pixels> {
	return page.locator('[data-editor-layer]').evaluate((node) => {
		const canvas = node as HTMLCanvasElement;
		const context = canvas.getContext('2d');
		if (!context || canvas.width < 2 || canvas.height < 2) return { width: canvas.width, height: canvas.height, samples: [] };
		const points = [
			[0.5, 0.5],
			[0.25, 0.25],
			[0.75, 0.25],
			[0.25, 0.75],
			[0.75, 0.75]
		];
		try {
			const samples = points.map(([fx, fy]) => Array.from(context.getImageData(Math.floor(canvas.width * fx), Math.floor(canvas.height * fy), 1, 1).data));
			return { width: canvas.width, height: canvas.height, samples };
		} catch (error) {
			return { width: canvas.width, height: canvas.height, samples: [], error: String(error) };
		}
	});
}

async function expectPicture(page: Page, label: string, size: { width: number; height: number }) {
	const canvas = page.locator('[data-editor-canvas]');
	await expect(canvas.getByText('Preview', { exact: true }), `${label}: the preview should finish`).toHaveCount(0, { timeout: 30000 });
	await expect(page.locator('[data-render-error]'), `${label}: no render error`).toHaveCount(0);
	let last: Pixels | null = null;
	await expect
		.poll(
			async () => {
				last = await canvasPixels(page);
				const shape = Math.abs(last.width / last.height - size.width / size.height) < 0.02;
				const lit = last.samples.length > 0 && last.samples.every(([r, g, b, a]) => a > 200 && Math.max(r, g, b) > 24);
				return shape && lit;
			},
			{ message: `${label}: the canvas should show a ${size.width}x${size.height} picture, not a black frame`, timeout: 20000 }
		)
		.toBe(true);
	console.log(`${label}: ${JSON.stringify(last)}`);
}

function rail(page: Page, tool: string) {
	return page.locator(`[data-tool-rail] button[data-tool="${tool}"]`);
}

function stackRows(page: Page) {
	return page.locator('[data-edit-stack="panel"] [data-stack-row]');
}

async function openPicture(page: Page, from: 'library' | 'history') {
	const token = await ownerToken(page);
	const name = `editor-real-${Date.now()}.png`;
	const buffer = solidPng(1920, 1072);
	const upload =
		from === 'library'
			? await page.request.post('/api/media/upload', { headers: { Authorization: `Bearer ${token}` }, multipart: { file: { name, mimeType: 'image/png', buffer } } })
			: await page.request.post('/api/generations/upload', { headers: { Authorization: `Bearer ${token}` }, multipart: { files: { name, mimeType: 'image/png', buffer } } });
	expect(upload.ok(), `upload -> ${upload.status()}`).toBeTruthy();

	await page.goto('/generate');
	await expect(page.locator('.tab-bar-container')).toBeVisible({ timeout: 15000 });
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
	const empty = page.getByTestId('tool-tab-empty');
	await expect(empty).toContainText('Open a picture to edit');
	if (from === 'library') {
		await empty.getByRole('button', { name: 'Library', exact: true }).click();
		await page.getByRole('button', { name: /^Use / }).first().click();
	} else {
		await empty.locator('[data-recent-history] button').first().click();
	}
	await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop', { timeout: 15000 });
}

async function editThrough(page: Page, label: string) {
	await expectPicture(page, `${label} opened`, { width: 1920, height: 1072 });

	await page.locator('[data-crop-aspects] button', { hasText: '1:1' }).click();
	await page.locator('[data-options-apply]').click();
	await expect(stackRows(page)).toHaveCount(2);
	await expectPicture(page, `${label} crop`, { width: 1072, height: 1072 });
	await screenshot(page, JOURNEY, `${label}-01-crop`);

	await rail(page, 'effects').click();
	await expect(page.locator('[data-look="reel"]')).toBeVisible({ timeout: 15000 });
	await page.locator('[data-look="reel"]').click();
	await page.locator('[data-options-apply]').click();
	await expect(stackRows(page)).toHaveCount(3);
	await expectPicture(page, `${label} look`, { width: 1072, height: 1072 });
	await screenshot(page, JOURNEY, `${label}-02-look`);

	await rail(page, 'canvas').click();
	await expect(page.locator('[data-catalog-tool="fit_canvas"]')).toBeVisible();
	await page.locator('[data-options-apply]').click();
	await expect(stackRows(page)).toHaveCount(4);
	await expectPicture(page, `${label} canvas`, { width: 1024, height: 1024 });
	await screenshot(page, JOURNEY, `${label}-03-canvas`);

	await rail(page, 'resize').click();
	await page.getByRole('button', { name: '50%', exact: true }).click();
	await page.locator('[data-options-apply]').click();
	await expect(stackRows(page)).toHaveCount(5);
	await expectPicture(page, `${label} resize`, { width: 512, height: 512 });
	await screenshot(page, JOURNEY, `${label}-04-resize`);
}

function expectClean(trace: Trace) {
	console.log(`console errors: ${JSON.stringify(trace.console)}`);
	console.log(`failed requests: ${JSON.stringify(trace.failed)}`);
	expect(trace.console).toEqual([]);
	expect(trace.failed.filter((line) => !line.endsWith('net::ERR_ABORTED'))).toEqual([]);
}

test.describe('the Image editor against the real backend', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	for (const from of ['library', 'history'] as const) {
		test(`a ${from} picture stays visible through a crop, a look, a server tool and a resize`, async ({ page }) => {
			test.setTimeout(120000);
			const trace = watch(page);
			await loginAsOwner(page);
			await openPicture(page, from);
			await editThrough(page, from);
			expectClean(trace);
		});
	}
});
