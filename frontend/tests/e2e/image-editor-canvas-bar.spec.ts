import { test, expect, type Locator, type Page, type Route } from '@playwright/test';
import { clearEdits, loginAsOwner, ownerToken, scopedStorageKey, screenshot, selectRailTool, railButton } from './helpers';
import { solidPng } from './mediaToolMocks';

const JOURNEY = 'image-editor-canvas-bar';
const GENERATION_ID = 'gen-e2e-dusk';
const FILE_ID = 501;
const SOURCE_FILE = `outputs/2026-11-01/${GENERATION_ID}/observatory-dusk.png`;

type Json = Record<string, unknown>;

const FORM_SCHEMA = {
	properties: {
		generation: {
			type: 'group',
			title: 'Generation',
			children: [
				{ name: 'prompt', type: 'string', title: 'Prompt', default: 'an observatory at dusk' },
				{ name: 'image', type: 'image', title: 'Reference image', allow_inpaint: true }
			]
		}
	}
};

interface Log {
	runs: Json[];
	artifacts: number;
	uploads: number;
	frames: Json[];
}

async function json(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

function generation(): Json {
	const now = new Date().toISOString();
	return {
		id: GENERATION_ID,
		preset_id: 'krea2',
		preset_name: 'Krea 2',
		mode: 'txt2img',
		form_data: {},
		status: 'completed',
		progress: 1,
		created_at: now,
		updated_at: now,
		completed_at: now,
		files: [
			{ id: FILE_ID, file_path: SOURCE_FILE, file_type: 'image', is_final: true, created_at: now, width: 640, height: 480 }
		]
	};
}

async function mockEditor(page: Page): Promise<Log> {
	const log: Log = { runs: [], artifacts: 0, uploads: 0, frames: [] };
	const source = solidPng(640, 480);
	const small = solidPng(64, 64);

	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
		route.request().method() === 'GET' ? json(route, { generations: [generation()], total: 1 }) : route.fallback()
	);
	await page.route(`**/api/media/generations/${GENERATION_ID}/observatory-dusk.png*`, (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: source })
	);
	await page.route('**/api/e2e-art/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: small }));
	await page.route(/\/api\/media-tools\/artifacts$/, async (route) => {
		log.artifacts += 1;
		await json(route, {
			id: `art-${log.artifacts}`,
			media_type: 'image',
			mime_type: 'image/png',
			url: `/api/e2e-art/art-${log.artifacts}.png`,
			width: 480,
			height: 480,
			duration_seconds: null,
			fps: null,
			size: 2048
		});
	});
	await page.route(/\/api\/media-tools\/preview\/frame$/, async (route) => {
		log.frames.push(route.request().postDataJSON() as Json);
		await route.fulfill({ status: 200, contentType: 'image/png', body: source });
	});
	await page.route(/\/api\/media-tools\/runs$/, async (route) => {
		if (route.request().method() !== 'POST') return route.fallback();
		log.runs.push(route.request().postDataJSON() as Json);
		await json(route, { run_id: `run-${log.runs.length}` }, 202);
	});
	await page.route(/\/api\/media-tools\/runs\/[^/]+$/, async (route) => {
		const id = route.request().url().split('/').pop() as string;
		await json(route, {
			run_id: id,
			status: 'completed',
			progress: 1,
			label: 'Saving',
			replaced: false,
			error: null,
			items: [
				{
					id: `copy-${id}`,
					filename: 'observatory-dusk-edit.png',
					original_filename: 'observatory-dusk-edit.png',
					media_type: 'image',
					mime_type: 'image/png',
					url: '/api/e2e-art/copy.png',
					width: 480,
					height: 480,
					size: 4096
				}
			]
		});
	});
	await page.route(/\/api\/media\/upload$/, async (route) => {
		log.uploads += 1;
		await json(route, {
			path: `uploads/sent-${log.uploads}.png`,
			relative_path: `uploads/sent-${log.uploads}.png`,
			filename: `sent-${log.uploads}.png`,
			url: `/api/e2e-art/sent-${log.uploads}.png`,
			width: 480,
			height: 480,
			size: 4096
		});
	});
	await page.route('**/api/presets/*/form*', (route) =>
		json(route, { preset_id: 'mock', form_schema: FORM_SCHEMA })
	);
	return log;
}

async function seedGenerationTab(page: Page) {
	const headers = { Authorization: `Bearer ${await ownerToken(page)}` };
	const list = await page.request.get('/api/presets?include_uninstalled=true', { headers });
	const raw = (await list.json()).data;
	const presets = (Array.isArray(raw) ? raw : raw?.presets || []) as Array<{ id: string }>;
	let found: { id: string; mode: string } | null = null;
	for (const preset of presets) {
		const res = await page.request.get(`/api/presets/${preset.id}/modes`, { headers });
		const body = await res.json();
		const mode = body?.data?.default_mode || body?.data?.modes?.[0]?.name;
		if (mode) {
			found = { id: preset.id, mode };
			break;
		}
	}
	test.skip(!found, 'no preset with a mode on this instance');
	const key = await scopedStorageKey(page, 'potionui_tabs_state');
	await page.addInitScript(
		({ key, preset }) => {
			try {
				if (sessionStorage.getItem('image-editor-tab-seeded')) return;
				sessionStorage.setItem('image-editor-tab-seeded', '1');
				const tab = {
					id: 'portrait-study',
					name: 'Portrait study',
					selectedPreset: preset.id,
					selectedMode: preset.mode,
					selectedVariant: null,
					selectedSessionId: null,
					activeGenerationId: null,
					prompt: '',
					negativePrompt: '',
					promptSegments: [],
					negativePromptSegments: [],
					formData: {}
				};
				localStorage.setItem(key, JSON.stringify({ tabs: [tab], activeTabId: tab.id }));
			} catch {
				return;
			}
		},
		{ key, preset: found! }
	);
}


async function stackRows(page: Page) {
	return page.locator('[data-edit-stack="panel"] [data-stack-row]').evaluateAll((rows) =>
		rows.map((row) => `${row.getAttribute('data-status')}${row.hasAttribute('data-hidden') ? ':hidden' : ''}:${row.textContent?.replace(/\s+/g, '')}`)
	);
}

const ORIGINAL = 'original:00Original640×480·PNG';
const CROP = '01Crop1:1·480×480';
const STAGE = '[data-paint-stage] canvas[aria-label="Drawing canvas"]';
const FLOATING = '[data-canvas-actions="floating"]';

async function start(page: Page, seed = false): Promise<Log> {
	const log = await mockEditor(page);
	await loginAsOwner(page);
	await clearEdits(page);
	if (seed) await seedGenerationTab(page);
	await page.goto('/generate');
	await expect(page.locator('.tab-bar-container')).toBeVisible({ timeout: 15000 });
	return log;
}

async function openDusk(page: Page) {
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
	const empty = page.getByTestId('tool-tab-empty');
	await expect(empty).toContainText('Open a picture to edit');
	await empty.locator('[data-start-source="history"]').click();
	await page.getByTestId('history-file-tile').first().locator('[data-pick-main]').click();
	await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop');
	await expect(page.locator('[data-crop-overlay]')).toBeVisible();
}

const bar = (page: Page) => page.locator(FLOATING);
const barButton = (page: Page, id: string) => page.locator(`[data-canvas-actions] [data-canvas-action="${id}"]`);

async function maskAlpha(page: Page, fx: number, fy: number): Promise<number> {
	return page.locator('[data-mask-layer]').evaluate(
		(canvas, [x, y]) =>
			(canvas as HTMLCanvasElement)
				.getContext('2d', { willReadFrequently: true })!
				.getImageData(Math.floor(x * (canvas as HTMLCanvasElement).width), Math.floor(y * (canvas as HTMLCanvasElement).height), 1, 1).data[3],
		[fx, fy] as [number, number]
	);
}

const SHAPE: Array<[number, number]> = [[0.55, 0.3], [0.8, 0.3], [0.86, 0.55], [0.6, 0.68], [0.5, 0.5]];

async function lasso(page: Page, path: Array<[number, number]> = SHAPE, finish = true) {
	const box = (await page.locator(STAGE).boundingBox())!;
	const at = ([fx, fy]: [number, number]) => ({ x: box.x + box.width * fx, y: box.y + box.height * fy });
	const [first, ...rest] = path;
	await page.mouse.move(at(first).x, at(first).y);
	await page.mouse.down();
	for (const point of rest) await page.mouse.move(at(point).x, at(point).y, { steps: 4 });
	if (finish) await page.mouse.up();
}

const squeezed = async (locator: Locator) => ((await locator.textContent()) ?? '').replace(/\s+/g, '');

test.describe('the canvas action bar on desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('a lasso selection brings the bar, and Edit this area hands its shape to Edit a region', async ({ page }) => {
		await start(page, true);
		await openDusk(page);
		await selectRailTool(page, 'paint.lasso');
		await expect(page.locator(STAGE)).toBeVisible();
		await expect(bar(page)).toHaveCount(0);

		await lasso(page, SHAPE, false);
		await expect(bar(page)).toHaveCount(0);
		await page.mouse.up();
		await expect(bar(page)).toBeVisible();
		await expect(bar(page).locator('[data-canvas-action]')).toHaveCount(6);
		for (const label of ['Cut', 'Copy', 'Delete', 'Invert', 'Deselect', 'Edit this area']) {
			await expect(bar(page).getByRole('button', { name: label })).toBeVisible();
		}
		await screenshot(page, JOURNEY, '02-lasso-fixed-1440');

		const size = page.locator('[data-selection-size]');
		const shaped = await squeezed(size);
		await barButton(page, 'invert').click();
		await expect.poll(() => squeezed(size)).not.toBe(shaped);
		await screenshot(page, JOURNEY, '40-zoom-top-right-inverted-1440');
		await barButton(page, 'invert').click();
		await expect.poll(() => squeezed(size)).toBe(shaped);

		await barButton(page, 'edit-area').click();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'edit_region');
		await expect(page.locator('[data-mask-layer]')).toBeVisible();
		await expect(page.locator('[data-mask-area]')).toBeVisible();
		await expect.poll(() => maskAlpha(page, 0.7, 0.45)).toBe(255);
		expect(await maskAlpha(page, 0.2, 0.2)).toBe(0);
		expect(await maskAlpha(page, 0.9, 0.9)).toBe(0);
		await expect(page.locator('[data-prompt-field]').first()).toBeFocused();
		await expect(barButton(page, 'clear')).toBeVisible();
		await screenshot(page, JOURNEY, '03-edit-region-marking-bar-1440');

		await barButton(page, 'invert').click();
		await expect.poll(() => maskAlpha(page, 0.7, 0.45)).toBe(0);
		expect(await maskAlpha(page, 0.2, 0.2)).toBe(255);

		await barButton(page, 'clear').click();
		await expect(page.locator('[data-mask-area]')).toBeHidden();
		await expect(barButton(page, 'clear')).toBeDisabled();
		expect(await maskAlpha(page, 0.2, 0.2)).toBe(0);
	});

	test('Deselect on the bar and Escape both clear the selection, and Escape also cancels a stroke', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await selectRailTool(page, 'paint.lasso');
		const size = page.locator('[data-selection-size]');

		await lasso(page);
		await expect(bar(page)).toBeVisible();
		await expect(size).not.toHaveText('none');
		await barButton(page, 'deselect').click();
		await expect(bar(page)).toHaveCount(0);
		await expect(size).toHaveText('none');

		await lasso(page);
		await expect(bar(page)).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(bar(page)).toHaveCount(0);
		await expect(size).toHaveText('none');

		await lasso(page, [[0.2, 0.2], [0.4, 0.2], [0.4, 0.5]], false);
		await page.keyboard.press('Escape');
		await page.mouse.up();
		await expect(bar(page)).toHaveCount(0);
		await expect(size).toHaveText('none');
	});

	test('Cancel on the crop bar puts the picture back, Apply crop lands the edit', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await page.locator('[data-options-panel] [data-crop-aspects] button', { hasText: '1:1' }).click();
		await expect(railButton(page, 'crop').locator('[data-tool-pending]')).toBeVisible();
		await expect(bar(page)).toBeVisible();
		await expect(bar(page).locator('[data-crop-aspects]')).toBeVisible();
		await expect(bar(page).locator('[data-crop-output]')).toHaveText('480×480');
		await screenshot(page, JOURNEY, '05-crop-1440');

		await barButton(page, 'cancel').click();
		await expect(railButton(page, 'crop').locator('[data-tool-pending]')).toHaveCount(0);
		await expect.poll(() => stackRows(page)).toEqual([`current:${ORIGINAL.replace('original:', '')}`]);

		await selectRailTool(page, 'crop');
		await bar(page).locator('[data-crop-aspects] button', { hasText: '1:1' }).click();
		await barButton(page, 'apply').click();
		await expect.poll(() => stackRows(page)).toEqual([ORIGINAL, `current:${CROP}`]);
	});

	test('paint mode shows the zoom top-right and keeps the picture clear of the bar', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await selectRailTool(page, 'paint.brush');
		await expect(page.locator('[data-paint-stage]')).toBeVisible();
		await expect(page.locator('[data-canvas-zoom]')).toBeVisible();
		await page.locator('[data-canvas-zoom] [data-canvas-actual]').click();
		await expect(page.locator('[data-canvas-zoom-value]')).toHaveText('100%');
		await page.locator('[data-canvas-zoom] [data-canvas-fit]').click();
		await screenshot(page, JOURNEY, '40-zoom-top-right-paint-1440');
	});

	test('the zoom sits in the top-right corner, the picture stays above the bar, and the bar collapses to icons on a narrow canvas', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await selectRailTool(page, 'paint.lasso');
		await lasso(page);
		await expect(bar(page)).toBeVisible();
		const barBox = (await bar(page).boundingBox())!;
		const zoom = page.locator('[data-canvas-zoom]');
		await expect(zoom).toBeVisible();
		const zoomBox = (await zoom.boundingBox())!;
		const stageBox = (await page.locator('[data-paint-stage]').boundingBox())!;
		expect(Math.abs(stageBox.x + stageBox.width - (zoomBox.x + zoomBox.width) - 12)).toBeLessThanOrEqual(1);
		expect(Math.abs(zoomBox.y - stageBox.y - 12)).toBeLessThanOrEqual(1);
		const doc = ((await page.locator('[data-doc-box]').getAttribute('data-doc-box')) ?? '').split(',').map(Number);
		expect(stageBox.y + doc[1] + doc[3]).toBeLessThanOrEqual(barBox.y + 1);
		await screenshot(page, JOURNEY, '40-zoom-top-right-lasso-1440');
		await expect(bar(page).locator('.cab-label').first()).toBeVisible();
		await page.setViewportSize({ width: 1024, height: 800 });
		await expect(bar(page).locator('.cab-label').first()).toBeHidden();
		await bar(page).getByRole('button', { name: 'Invert' }).hover();
		await expect(page.getByRole('tooltip').filter({ hasText: 'Invert' })).toBeVisible();
		await screenshot(page, JOURNEY, '09-narrow-1024');
	});
});

test.describe('the canvas action bar on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('a full-width strip sits above the tool sheet', async ({ page }) => {
		await mockEditor(page);
		await loginAsOwner(page);
		await clearEdits(page);
		await page.goto('/generate');
		await expect(page.locator('.studio-topbar')).toBeVisible({ timeout: 15000 });
		await page.getByRole('button', { name: /^Open tabs/ }).click();
		await page.getByRole('dialog', { name: 'Tabs' }).getByRole('button', { name: 'Image editor' }).click();
		const phone = page.getByTestId('phone-tool-tab');
		await phone.locator('[data-start-source="history"]').click();
		await page.getByTestId('history-file-tile').first().locator('[data-pick-main]').click();
		await expect(phone.locator('[data-editor-frame="phone"]')).toBeVisible();
		await phone.locator('[data-tool-rail="strip"] [data-group-button="select"]').click();
		await page.locator('[data-rail-sheet="select"] [data-sheet-item="paint.lasso"]').click();
		await expect(phone.locator(STAGE)).toBeVisible();
		await expect(phone.locator('[data-canvas-actions="strip"]')).toHaveCount(0);

		await lasso(page);
		const strip = phone.locator('[data-canvas-actions="strip"]');
		await expect(strip).toBeVisible();
		await expect(strip.getByRole('button', { name: 'Edit this area' })).toBeVisible();
		const stripBox = (await strip.boundingBox())!;
		const panelBox = (await phone.locator('[data-options-panel="phone"]').boundingBox())!;
		expect(stripBox.y + stripBox.height).toBeLessThanOrEqual(panelBox.y + 1);
		expect(stripBox.width).toBeGreaterThan(300);
		await screenshot(page, JOURNEY, '07-phone-lasso-390');

		const overflowX = await page.evaluate(() => document.scrollingElement!.scrollWidth - document.scrollingElement!.clientWidth);
		expect(overflowX).toBeLessThanOrEqual(0);
	});
});
