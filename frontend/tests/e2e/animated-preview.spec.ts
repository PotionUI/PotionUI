import { test, expect, type Page, type WebSocketRoute } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { installAndSelectImagePreset, discoverAndAssignImagePreset, presetOption } from './presetPreamble';
import { makeModelFieldsOptional, generationStatusMessage } from './generationMocks';

const JOURNEY = 'animated-preview';
const GENERATION_ID = '01ANIMATEDPREVIEW00000001';
const TOTAL_STEPS = 30;
const FRAME_W = 384;
const FRAME_H = 216;

async function makeSprite(page: Page, noise: number): Promise<string> {
	return page.evaluate(
		({ w, h, noise }) => {
			const columns = 5;
			const frames = 25;
			const sheet = document.createElement('canvas');
			sheet.width = w * columns;
			sheet.height = h * columns;
			const ctx = sheet.getContext('2d')!;
			let seed = 7;
			const rand = () => {
				seed = (seed * 16807) % 2147483647;
				return seed / 2147483647;
			};
			for (let i = 0; i < frames; i++) {
				const ox = (i % columns) * w;
				const oy = Math.floor(i / columns) * h;
				const sky = ctx.createLinearGradient(0, oy, 0, oy + h * 0.6);
				sky.addColorStop(0, '#2b3a5c');
				sky.addColorStop(1, '#e08a4a');
				ctx.fillStyle = sky;
				ctx.fillRect(ox, oy, w, h * 0.6);
				ctx.fillStyle = '#101722';
				ctx.fillRect(ox, oy + h * 0.6, w, h * 0.4);
				const sunX = ox + w * (0.3 + 0.4 * (i / (frames - 1)));
				const sun = ctx.createRadialGradient(sunX, oy + h * 0.55, 2, sunX, oy + h * 0.55, 46);
				sun.addColorStop(0, '#fff2cc');
				sun.addColorStop(1, 'rgba(255,200,120,0)');
				ctx.fillStyle = sun;
				ctx.fillRect(ox, oy, w, h);
				const blocks = Math.round(2400 * noise);
				for (let b = 0; b < blocks; b++) {
					const v = Math.floor(rand() * 255);
					ctx.fillStyle = `rgba(${v},${v},${v},0.35)`;
					ctx.fillRect(ox + rand() * w, oy + rand() * h, 4, 4);
				}
			}
			return sheet.toDataURL('image/webp', 0.7);
		},
		{ w: FRAME_W, h: FRAME_H, noise }
	);
}

function clipMessage(image: string, step: number, admin = false) {
	return {
		type: 'workbench_preview_clip',
		generation_id: GENERATION_ID,
		pipe_id: 1,
		pipe_name: 'generator',
		output_type: 'preview_clip',
		temporary: true,
		image,
		frames: 25,
		columns: 5,
		frame_width: FRAME_W,
		frame_height: FRAME_H,
		fps: 8,
		step,
		total_steps: TOTAL_STEPS,
		source_indices: Array.from({ length: 25 }, (_, i) => i * 4),
		...(admin
			? { admin_detail: { format: 'WEBP', bytes: 217088, long_edge: 384, update: 3, updates_max: 8, every: 3 } }
			: {})
	};
}

const statusMsg = (stepNum: number) =>
	generationStatusMessage({
		generationId: GENERATION_ID,
		pipeId: 1,
		pipeName: 'generator',
		currentStep: `Sampling <<PROGRESS:${stepNum}/${TOTAL_STEPS}>>`,
		progress: stepNum / TOTAL_STEPS,
		stepNum,
		totalSteps: TOTAL_STEPS
	});

async function mockBackend(page: Page, sockets: WebSocketRoute[]) {
	await page.routeWebSocket(/\/ws\/generation/, (ws) => {
		sockets.push(ws);
		ws.send(JSON.stringify({ type: 'connection_established', client_id: 'e2e-mock' }));
		ws.onMessage((raw) => {
			const msg = JSON.parse(raw as string);
			if (msg.type === 'subscribe_generation') {
				ws.send(JSON.stringify({ type: 'subscribed', generation_id: msg.generation_id }));
			}
		});
	});
	await page.route('**/api/generations/start', async (route) => {
		await route.fulfill({
			json: {
				success: true,
				data: {
					generation_id: GENERATION_ID,
					status: {
						id: GENERATION_ID,
						generation_id: GENERATION_ID,
						status: 'pending',
						progress: 0.0,
						created_at: new Date().toISOString()
					},
					queue_position: null
				}
			}
		});
	});
	await makeModelFieldsOptional(page);
}

async function startOnDesktop(page: Page, sockets: WebSocketRoute[]): Promise<WebSocketRoute | null> {
	await mockBackend(page, sockets);
	await loginAsOwner(page);
	const preset = await installAndSelectImagePreset(page);
	if (!preset) return null;
	const promptEditor = page.locator('.inline-chip-editor[role="textbox"]').first();
	await expect(promptEditor).toBeVisible({ timeout: 15000 });
	await promptEditor.click();
	await page.keyboard.type('a sailboat crossing a sunset sea');
	const generateButton = page.getByRole('button', { name: 'Generate', exact: true });
	await expect(generateButton).toBeEnabled({ timeout: 15000 });
	await generateButton.click();
	await expect.poll(() => sockets.length, { timeout: 15000 }).toBeGreaterThan(0);
	return sockets[sockets.length - 1];
}

const stageTop = (page: Page) =>
	page.locator('#workbench-container').first().evaluate((el) => el.scrollIntoView({ block: 'start' }));

const mediaBottom = (page: Page) =>
	page.locator('#workbench-container > div.isolate').first().evaluate((el) => el.getBoundingClientRect().bottom);

const canvasHash = (page: Page) =>
	page.evaluate(() => {
		const canvas = document.querySelector('[data-preview-clip] canvas') as HTMLCanvasElement;
		return canvas.toDataURL('image/png').length + ':' + canvas.toDataURL('image/png').slice(-64);
	});

test.describe('desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('the Workbench loops the preview clip, swaps clips in place and hands over to the video', async ({ page }) => {
		const sockets: WebSocketRoute[] = [];
		const ws = await startOnDesktop(page, sockets);
		if (!ws) {
			test.skip(true, 'No native image preset available on this throwaway instance.');
			return;
		}
		const send = (message: object) => ws.send(JSON.stringify(message));
		const noisy = await makeSprite(page, 1);
		const clearer = await makeSprite(page, 0.3);

		send(statusMsg(4));
		send(clipMessage(noisy, 3));

		const clipRoot = page.locator('[data-preview-clip]');
		await expect(clipRoot).toBeVisible({ timeout: 10000 });
		await expect(page.locator('[data-preview-chip]')).toContainText(/preview · step 3/i);
		const status = page.locator('[data-preview-status]');
		await expect(status).toContainText('Preview');
		await expect(status).not.toContainText(/frames|KB|fps|every/i);
		await expect(page.locator('[data-preview-format]')).toHaveCount(0);
		await expect(page.locator('[data-preview-ruler]')).toBeVisible();
		await stageTop(page);
		await screenshot(page, JOURNEY, '01-early-sampling-1440');

		const first = await canvasHash(page);
		await expect.poll(() => canvasHash(page), { timeout: 5000 }).not.toBe(first);

		const mediaArea = clipRoot.locator('xpath=..').locator('xpath=..');
		const beforeStatusBox = (await status.boundingBox())!;
		await page.evaluate(() => {
			(window as any).__clipCanvas = document.querySelector('[data-preview-clip] canvas');
		});

		send(statusMsg(17));
		send(clipMessage(clearer, 15));
		await expect(page.locator('[data-preview-chip]')).toContainText(/preview · step 15/i);
		expect(
			await page.evaluate(
				() => (window as any).__clipCanvas === document.querySelector('[data-preview-clip] canvas')
			),
			'the clip swaps inside the same canvas, nothing remounts'
		).toBe(true);
		const afterStatusBox = (await status.boundingBox())!;
		expect(Math.abs(afterStatusBox.y - beforeStatusBox.y)).toBeLessThanOrEqual(1);
		expect(Math.abs(afterStatusBox.height - beforeStatusBox.height)).toBeLessThanOrEqual(1);
		await expect(mediaArea).toBeVisible();

		const control = page.locator('[data-preview-control]');
		await expect(control).toHaveCSS('opacity', '0');
		await clipRoot.hover();
		await expect(control).toHaveCSS('opacity', '1');
		await page.getByRole('button', { name: 'Pause preview' }).click();
		await expect(clipRoot).toHaveAttribute('data-paused', 'true');
		const held = await canvasHash(page);
		await page.waitForTimeout(600);
		expect(await canvasHash(page)).toBe(held);
		await stageTop(page);
		await screenshot(page, JOURNEY, '02-mid-sampling-hover-paused-1440');
		await page.getByRole('button', { name: 'Resume preview' }).click();
		await expect(clipRoot).toHaveAttribute('data-paused', 'false');
		await page.keyboard.press('Space');
		await expect(clipRoot).toHaveAttribute('data-paused', 'true');
		await page.keyboard.press('Space');
		await expect(clipRoot).toHaveAttribute('data-paused', 'false');

		await page.mouse.move(5, 5);
		send(statusMsg(18));
		send(clipMessage(clearer, 18, true));
		await expect(page.locator('[data-preview-format]')).toHaveText('384×216 · 25 fr · 8 fps');
		await expect(status).toContainText('Preview · 25 frames · new clip every 3 steps · 212 KB');
		await stageTop(page);
		await screenshot(page, JOURNEY, '03-admin-variant-1440');

		send(clipMessage(clearer, 18));
		await expect(page.locator('[data-preview-format]')).toHaveCount(0);

		await page.keyboard.press('w');
		const overlay = page.getByRole('dialog', { name: 'Workbench' });
		await expect(overlay).toBeVisible({ timeout: 5000 });
		await expect(overlay.locator('[data-preview-clip]')).toBeVisible();
		await expect(overlay.locator('[data-preview-status]')).toContainText('Preview');
		await stageTop(page);
		await screenshot(page, JOURNEY, '04-floating-workbench-1440');
		await page.keyboard.press('Escape');
		await expect(overlay).toBeHidden();

		const statusBox = (await status.boundingBox())!;
		const statusGap = statusBox.y - (await mediaBottom(page));
		send({
			type: 'gallery_update',
			generation_id: GENERATION_ID,
			pipe_id: 2,
			pipe_name: 'gallery',
			output_type: 'gallery',
			videos: [{ temporary: false }],
			video_urls_list: [{ path: '/api/media/generations/none/0.mp4', duration: 5, fps: 16, resolution: '832x480' }]
		});
		send({
			type: 'generation_complete',
			data: {
				id: GENERATION_ID,
				generation_id: GENERATION_ID,
				status: 'completed',
				progress: 1.0,
				created_at: new Date().toISOString(),
				completed_at: new Date().toISOString()
			}
		});
		await expect(page.locator('[data-preview-clip]')).toHaveCount(0, { timeout: 10000 });
		await expect(page.locator('[data-preview-status]')).toHaveCount(0);
		const playButton = page.getByRole('button', { name: /Play video|Pause video/ });
		await expect(playButton).toBeVisible({ timeout: 10000 });
		const controlsBar = playButton.locator('xpath=ancestor::div[contains(@class,"border-t")][1]');
		const controlsBox = (await controlsBar.boundingBox())!;
		const controlsGap = controlsBox.y - (await mediaBottom(page));
		expect(Math.abs(controlsGap - statusGap), `controls take the status bar slot (${controlsGap} vs ${statusGap}; heights ${controlsBox.height} vs ${statusBox.height})`).toBeLessThanOrEqual(1);
		expect(Math.abs(controlsBox.height - statusBox.height), 'no layout shift').toBeLessThanOrEqual(1);
		await screenshot(page, JOURNEY, '05-completed-1440');
	});

	test('reduced motion holds the latest frame and offers no pause control', async ({ page }) => {
		await page.emulateMedia({ reducedMotion: 'reduce' });
		const sockets: WebSocketRoute[] = [];
		const ws = await startOnDesktop(page, sockets);
		if (!ws) {
			test.skip(true, 'No native image preset available on this throwaway instance.');
			return;
		}
		const sprite = await makeSprite(page, 0.5);
		ws.send(JSON.stringify(statusMsg(6)));
		ws.send(JSON.stringify(clipMessage(sprite, 6)));
		const clipRoot = page.locator('[data-preview-clip]');
		await expect(clipRoot).toHaveAttribute('data-reduced', 'true', { timeout: 10000 });
		await expect(page.locator('[data-preview-control]')).toHaveCount(0);
		const held = await canvasHash(page);
		await page.waitForTimeout(700);
		expect(await canvasHash(page)).toBe(held);
	});
});

test.describe('phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('the phone Workbench carries the chip and the ruler above the deck', async ({ page }) => {
		const sockets: WebSocketRoute[] = [];
		await mockBackend(page, sockets);
		await loginAsOwner(page);
		const preset = await discoverAndAssignImagePreset(page);
		if (!preset) {
			test.skip(true, 'No native image preset available on this throwaway instance.');
			return;
		}
		await page.goto('/generate');
		await expect(page.locator('.studio-dock')).toBeVisible({ timeout: 15000 });
		await page.getByRole('button', { name: 'Open preset and session' }).click();
		const sheet = page.getByRole('dialog', { name: 'Preset and session' });
		await expect(sheet).toBeVisible({ timeout: 5000 });
		await sheet.locator('button[aria-haspopup="dialog"]', { hasText: 'Choose a preset' }).click();
		await expect(page.locator('[role="listbox"][aria-label="Presets"]')).toBeVisible({ timeout: 15000 });
		await presetOption(page, preset.name).click();
		await expect(page.getByRole('dialog', { name: 'Choose preset' })).toBeHidden();
		await page.keyboard.press('Escape');
		await expect(sheet).toHaveCount(0);
		await page.getByRole('button', { name: 'Prompt', exact: true }).click();
		const editor = page.locator('[contenteditable="true"]').first();
		await expect(editor).toBeVisible({ timeout: 8000 });
		await editor.click();
		await page.keyboard.type('a sailboat crossing a sunset sea');
		await page.keyboard.press('Escape');
		const disc = page.locator('.generation-panel .generate-button');
		await expect(disc).toBeEnabled({ timeout: 15000 });
		await disc.click();
		await expect.poll(() => sockets.length, { timeout: 15000 }).toBeGreaterThan(0);
		const ws = sockets[sockets.length - 1];
		const sprite = await makeSprite(page, 0.6);
		ws.send(JSON.stringify(statusMsg(17)));
		ws.send(JSON.stringify(clipMessage(sprite, 15)));
		await expect(page.locator('[data-preview-clip]')).toBeVisible({ timeout: 10000 });
		await expect(page.locator('[data-preview-chip]')).toContainText(/preview · step 15/i);
		const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
		expect(overflow, 'no horizontal page scroll').toBeLessThanOrEqual(0);
		await screenshot(page, JOURNEY, '06-mobile-deck-390');
	});
});
