import { test, expect, type Page, type Route } from '@playwright/test';
import { clearEdits, editorMenu, loginAsOwner, ownerToken, scopedStorageKey, screenshot, selectRailTool, railButton } from './helpers';
import { solidPng } from './mediaToolMocks';

const JOURNEY = 'image-editor-tab';
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

function bookTab(page: Page, name: string | RegExp) {
	return page.locator('button.book-tab', { hasText: name });
}

function rail(page: Page, tool: string) {
	return page.locator(`[data-tool-rail] button[data-tool="${tool}"]`);
}

async function openEditorOnDusk(page: Page) {
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
	const empty = page.getByTestId('tool-tab-empty');
	await expect(empty).toContainText('Open a picture to edit');
	await expect(empty.locator('[data-start-source="history"]')).toBeVisible();
	return empty;
}

async function stackRows(page: Page) {
	return page.locator('[data-edit-stack="panel"] [data-stack-row]').evaluateAll((rows) =>
		rows.sort((a, b) => Number(a.getAttribute('data-stack-index')) - Number(b.getAttribute('data-stack-index'))).map((row) => `${row.getAttribute('data-status')}${row.hasAttribute('data-hidden') ? ':hidden' : ''}:${row.textContent?.replace(/\s+/g, '')}`)
	);
}

const ORIGINAL = 'original:Original640×480·PNG';
const CROP = '01Crop1:1·480×480';
const REEL = '02ReelIntensity100';

async function start(page: Page, seed = false): Promise<Log> {
	const log = await mockEditor(page);
	await loginAsOwner(page);
	await clearEdits(page);
	if (seed) await seedGenerationTab(page);
	await page.goto('/generate');
	await expect(page.locator('.tab-bar-container')).toBeVisible({ timeout: 15000 });
	return log;
}

async function openDusk(page: Page, shoot = false) {
	const empty = await openEditorOnDusk(page);
	if (shoot) await screenshot(page, JOURNEY, '06-empty-1440');
	await empty.locator('[data-start-source="history"]').click();
	await page.getByTestId('history-file-tile').first().locator('[data-pick-main]').click();
	await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop');
	await expect(page.locator('[data-crop-overlay]')).toBeVisible();
}

async function cropSquare(page: Page, shoot = false) {
	await page.locator('[data-options-panel] [data-crop-aspects] button', { hasText: '1:1' }).click();
	await expect(page.locator('[data-options-panel] [data-crop-output]')).toHaveText('480×480');
	await expect(railButton(page, 'crop').locator('[data-tool-pending]')).toBeVisible();
	if (shoot) await screenshot(page, JOURNEY, '02-crop-1440');
	await page.locator('[data-options-apply]').click();
	await expect.poll(() => stackRows(page)).toEqual([ORIGINAL, `current:${CROP}`]);
}

async function applyReel(page: Page, shoot = false) {
	await selectRailTool(page, 'effects');
	await expect(page.locator('[data-look-item="reel"]')).toBeVisible({ timeout: 15000 });
	await page.locator('[data-look-item="reel"]').click();
	await expect(page.locator('[data-stack-row="pending"]')).toContainText('Reel');
	await editorMenu(page, 'View', 'Before / after');
	await expect(page.locator('[data-canvas-divider]')).toBeVisible();
	await expect(page.locator('[data-editor-before]')).toBeVisible();
	if (shoot) await screenshot(page, JOURNEY, '03-effects-compare-1440');
	await page.locator('[data-options-apply]').click();
	await expect.poll(() => stackRows(page)).toEqual([ORIGINAL, `applied:${CROP}`, `current:${REEL}`]);
}

test.describe('the Image editor tab on desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('crop and a look, undo and redo, hide a step, then save as new and save the copy', async ({ page }) => {
		const log = await start(page);
		await openDusk(page, true);
		await expect(bookTab(page, 'observatory-dusk')).toBeVisible();
		await expect(page.locator('[data-frame-file]')).toHaveText('observatory-dusk.png');
		await cropSquare(page, true);
		await applyReel(page, true);
		await expect(bookTab(page, 'observatory-dusk').getByTestId('tab-unsaved')).toBeVisible();

		await page.locator('[data-frame-undo]').click();
		await expect.poll(() => stackRows(page)).toEqual([ORIGINAL, `current:${CROP}`, 'undone:02ReelUndone']);
		await page.locator('[data-frame-undo]').hover();
		await screenshot(page, JOURNEY, '04-edit-stack-1440');
		await page.keyboard.press('Control+Shift+Z');
		await expect.poll(() => stackRows(page)).toEqual([ORIGINAL, `applied:${CROP}`, `current:${REEL}`]);

		await page.locator('[data-stack-hide="s1"]').click();
		await expect(page.locator('[data-stack-row="s1"]')).toHaveAttribute('data-hidden', 'true');
		await page.locator('[data-frame-save-menu]').click();
		await expect(page.locator('[data-save-row="replace"]')).toBeDisabled();
		await screenshot(page, JOURNEY, '05-save-menu-1440');
		await page.keyboard.press('Escape');

		await page.locator('[data-frame-save]').click();
		await expect.poll(() => log.runs.length).toBe(1);
		expect(log.runs[0]).toEqual({
			steps: [
				{ tool: 'crop', params: { x: 80, y: 0, width: 480, height: 480 }, hidden: true },
				{ tool: 'paint_layer', params: { layer: { item_id: 'art-1' } } }
			],
			inputs: [{ generation_file_id: String(FILE_ID) }],
			output: { mode: 'new', name: 'observatory-dusk', edit: { id: expect.any(String), revision: expect.any(Number) } }
		});
		await expect(page.getByText('Saved to Library').first()).toBeVisible();
		await expect(page.locator('[data-frame-save]')).toHaveText(/^\s*Save\s*$/);
		await expect(bookTab(page, 'observatory-dusk').getByTestId('tab-unsaved')).toHaveCount(0);

		await page.locator('[data-stack-hide="s1"]').click();
		await expect(page.locator('[data-stack-row="s1"]')).not.toHaveAttribute('data-hidden', 'true');
		await page.locator('[data-frame-save]').click();
		await expect.poll(() => log.runs.length).toBe(2);
		expect(log.runs[1]).toEqual({
			steps: [
				{ tool: 'crop', params: { x: 80, y: 0, width: 480, height: 480 } },
				{ tool: 'paint_layer', params: { layer: { item_id: 'art-2' } } }
			],
			inputs: [{ generation_file_id: String(FILE_ID) }],
			output: { mode: 'update', name: 'observatory-dusk', target_item_id: 'copy-run-1', edit: { id: expect.any(String), revision: expect.any(Number) } }
		});
	});

	test('Send to a generation lands the edit in that tab field and switches to it', async ({ page }) => {
		const log = await start(page, true);
		await openDusk(page);
		await cropSquare(page);
		await page.locator('[data-frame-save-menu]').click();
		const send = page.locator('[data-save-row="send"]');
		await expect(send).toHaveCount(1);
		await expect(send).toContainText('Reference image');
		await send.click();
		await expect(page.getByTestId('tool-tab-host')).toHaveCount(0);
		await expect(bookTab(page, 'Portrait study')).toHaveClass(/active/);
		expect(log.uploads).toBe(1);
		const field = page.locator('[data-field-name="image"]').first();
		await expect(field).toContainText('observatory-dusk-edit.png');
		await expect(field.locator('img[src*="sent-1.png"]').first()).toBeVisible();
		await screenshot(page, JOURNEY, 'sent-to-generation-1440');
	});

	test('the stack and the saved copy survive a reload, and a catalog tool previews through the server', async ({ page }) => {
		const log = await start(page);
		await openDusk(page);
		await cropSquare(page);
		await applyReel(page);
		await page.locator('[data-frame-save]').click();
		await expect.poll(() => log.runs.length).toBe(1);
		await expect(page.locator('[data-frame-save]')).toHaveText(/^\s*Save\s*$/);

		await page.reload();
		await expect(bookTab(page, 'observatory-dusk')).toBeVisible({ timeout: 15000 });
		await expect.poll(() => stackRows(page), { timeout: 15000 }).toEqual([ORIGINAL, `applied:${CROP}`, `current:${REEL}`]);
		await expect(page.locator('[data-frame-save]')).toHaveText(/^\s*Save\s*$/);

		await selectRailTool(page, 'colorKey');
		await expect(page.locator('[data-catalog-tool="color_key"]')).toBeVisible();
		await screenshot(page, JOURNEY, '12-catalog-tool-color-key-1440');
		await page.locator('[data-options-apply]').click();
		await expect.poll(() => log.frames.length).toBeGreaterThan(0);
		const frame = log.frames[log.frames.length - 1] as { input: Json; steps: Array<{ tool: string }>; format: string };
		expect(frame.input).toEqual({ generation_file_id: String(FILE_ID) });
		expect(frame.steps.map((step) => step.tool)).toEqual(['crop', 'paint_layer', 'color_key']);
		expect(frame.format).toBe('png');
		await expect.poll(() => stackRows(page)).toHaveLength(4);
	});
});

test.describe('painting and masking in the Image editor tab', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('a stroke makes one paint layer and the mask rides along with Send', async ({ page }) => {
		const log = await mockEditor(page);
		await loginAsOwner(page);
		await clearEdits(page);
		await seedGenerationTab(page);
		await page.goto('/generate');
		await expect(page.locator('.tab-bar-container')).toBeVisible({ timeout: 15000 });
		const empty = await openEditorOnDusk(page);
		await empty.locator('[data-start-source="history"]').click();
		await page.getByTestId('history-file-tile').first().locator('[data-pick-main]').click();
		await expect(page.locator('[data-crop-overlay]')).toBeVisible();

		await selectRailTool(page, 'paint.brush');
		const stage = page.locator('[data-paint-stage] canvas[aria-label="Drawing canvas"]');
		await expect(stage).toBeVisible();
		await expect(page.locator('[data-paint-tool-panel="brush"]')).toBeVisible();
		await expect(page.locator('[data-paint-tools]')).toHaveCount(0);
		const box = (await stage.boundingBox())!;
		const cx = box.x + box.width / 2;
		const cy = box.y + box.height / 2;
		await page.mouse.move(cx - 80, cy);
		await page.mouse.down();
		for (let i = 1; i <= 8; i++) await page.mouse.move(cx - 80 + i * 20, cy + i * 6);
		await page.mouse.up();
		await expect(page.locator('[data-frame-undo]')).toBeEnabled();
		await screenshot(page, JOURNEY, 'paint-1440');
		await expect.poll(() => stackRows(page)).toEqual(['original:Original640×480·PNG', 'current:Paint1Paintlayer100%']);
		await expect.poll(() => log.artifacts).toBe(1);

		await selectRailTool(page, 'edit_region');
		const mask = page.locator('[data-mask-layer]');
		await expect(mask).toBeVisible();
		await options(page).getByRole('button', { name: 'Brush', exact: true }).click();
		const area = (await mask.boundingBox())!;
		await page.mouse.move(area.x + area.width * 0.3, area.y + area.height * 0.4);
		await page.mouse.down();
		await page.mouse.move(area.x + area.width * 0.6, area.y + area.height * 0.5, { steps: 6 });
		await page.mouse.up();
		await screenshot(page, JOURNEY, 'mask-1440');
		await expect(page.locator('[data-options-apply]')).toHaveCount(0);
		expect(log.uploads).toBe(0);
		await expect.poll(() => stackRows(page)).toHaveLength(2);

		await page.locator('[data-frame-save-menu]').click();
		const send = page.locator('[data-save-row="send"]');
		await expect(send).toContainText('MASK');
		await send.click();
		await expect(bookTab(page, 'Portrait study')).toHaveClass(/active/);
		await expect.poll(() => log.uploads).toBe(2);
		await expect(page.locator('[data-field-name="image"]').first().locator('img[src*="sent-2.png"]').first()).toBeVisible();
	});
});

test.describe('the Image editor tab on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('docks the options above the tool strip with Save in the top bar', async ({ page }) => {
		await mockEditor(page);
		await loginAsOwner(page);
		await clearEdits(page);
		await page.goto('/generate');
		await expect(page.locator('.studio-topbar')).toBeVisible({ timeout: 15000 });
		await page.getByRole('button', { name: /^Open tabs/ }).click();
		await page.getByRole('dialog', { name: 'Tabs' }).getByRole('button', { name: 'Image editor' }).click();
		const phone = page.getByTestId('phone-tool-tab');
		await expect(phone.getByTestId('tool-tab-empty')).toContainText('Open a picture to edit');
		await phone.locator('[data-start-source="history"]').click();
		await page.getByTestId('history-file-tile').first().locator('[data-pick-main]').click();

		await expect(phone.locator('[data-editor-frame="phone"]')).toBeVisible();
		await expect(phone.locator('[data-tool-rail="strip"]')).toBeVisible();
		await expect(phone.locator('[data-options-panel="phone"]')).toBeVisible();
		await phone.locator('[data-options-panel] [data-crop-aspects] button', { hasText: '1:1' }).click();
		await phone.locator('[data-options-apply]').click();
		await expect(phone.locator('[data-frame-edits]')).toContainText('1 edit');
		await phone.locator('[data-tool-rail="strip"] button[data-tool="effects"]').click();
		await expect(phone.locator('[data-look-item="reel"]')).toBeVisible({ timeout: 15000 });
		await phone.locator('[data-look-item="reel"]').click();
		await screenshot(page, JOURNEY, '08-phone-effects-390');

		const overflowX = await page.evaluate(() => document.scrollingElement!.scrollWidth - document.scrollingElement!.clientWidth);
		expect(overflowX).toBeLessThanOrEqual(0);
		await expect(phone.getByRole('button', { name: 'Undo Reel' })).toBeEnabled();
		await phone.getByRole('button', { name: 'Save', exact: true }).click();
		const sheet = page.getByRole('dialog', { name: 'Save' });
		await expect(sheet.locator('[data-save-row="save-new"]')).toBeVisible();
		await expect(sheet.locator('[data-save-row="replace"]')).toBeDisabled();
		await screenshot(page, JOURNEY, '05-save-sheet-390');
		await page.keyboard.press('Escape');
		await phone.locator('[data-frame-edits]').click();
		await expect(page.getByRole('dialog', { name: 'Edits' })).toBeVisible();
		await screenshot(page, JOURNEY, '04-edits-sheet-390');
	});
});

async function maskAlpha(page: Page, fx: number, fy: number): Promise<number> {
	return page.locator('[data-mask-layer]').evaluate(
		(canvas, [x, y]) =>
			(canvas as HTMLCanvasElement)
				.getContext('2d', { willReadFrequently: true })!
				.getImageData(Math.floor(x * (canvas as HTMLCanvasElement).width), Math.floor(y * (canvas as HTMLCanvasElement).height), 1, 1).data[3],
		[fx, fy] as [number, number]
	);
}

async function drag(page: Page, path: Array<[number, number]>, finish = true) {
	const box = (await page.locator('[data-mask-layer]').boundingBox())!;
	const at = ([fx, fy]: [number, number]) => ({ x: box.x + box.width * fx, y: box.y + box.height * fy });
	const [first, ...rest] = path;
	await page.mouse.move(at(first).x, at(first).y);
	await page.mouse.down();
	for (const point of rest) await page.mouse.move(at(point).x, at(point).y, { steps: 4 });
	if (finish) await page.mouse.up();
}

const options = (page: Page) => page.locator('[data-options-panel]');

test.describe('the lasso in the Image editor tab', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('a lasso and a brush mark one area that rides along with Send', async ({ page }) => {
		const log = await start(page, true);
		await openDusk(page);
		await cropSquare(page);

		await selectRailTool(page, 'edit_region');
		await expect(page.locator('[data-mask-layer]')).toBeVisible();
		await expect(options(page).getByRole('group', { name: 'Marking tool' })).toBeVisible();
		await expect(options(page).getByRole('group', { name: 'Lasso mode' })).toBeVisible();
		await options(page).getByRole('button', { name: 'Brush', exact: true }).click();
		await expect(options(page).getByRole('group', { name: 'Mask brush' })).toBeVisible();
		await expect(options(page)).toContainText('Brush size');

		await drag(page, [[0.2, 0.3], [0.32, 0.3]]);
		await expect.poll(() => maskAlpha(page, 0.26, 0.3)).toBe(255);
		await screenshot(page, JOURNEY, '10b-mask-brush-1440');

		await options(page).getByRole('button', { name: 'Lasso', exact: true }).click();
		await expect(options(page).getByRole('group', { name: 'Lasso mode' })).toBeVisible();
		await expect(options(page)).toContainText('Release to close the shape.');
		await expect(options(page)).not.toContainText('Brush size');

		const shape: Array<[number, number]> = [[0.55, 0.3], [0.8, 0.3], [0.86, 0.55], [0.6, 0.68], [0.5, 0.5]];
		await drag(page, shape, false);
		await expect(page.locator('[data-lasso-outline]')).toBeVisible();
		await page.mouse.up();
		await expect(page.locator('[data-lasso-outline]')).toHaveCount(0);
		await expect(page.locator('[data-mask-area]')).toBeVisible();
		await expect(options(page).locator('[data-mask-area-readout]')).not.toContainText('None');
		await expect.poll(() => maskAlpha(page, 0.7, 0.45)).toBe(255);
		await expect.poll(() => maskAlpha(page, 0.26, 0.3)).toBe(255);
		await screenshot(page, JOURNEY, '10-mask-lasso-1440');

		await options(page).getByRole('button', { name: 'Subtract', exact: true }).click();
		await drag(page, [[0.64, 0.4], [0.74, 0.4], [0.74, 0.5], [0.64, 0.5]]);
		await expect.poll(() => maskAlpha(page, 0.69, 0.45)).toBe(0);
		expect(await maskAlpha(page, 0.78, 0.4)).toBe(255);
		expect(await maskAlpha(page, 0.26, 0.3)).toBe(255);

		await options(page).getByRole('button', { name: 'Add', exact: true }).click();
		const before = await options(page).locator('[data-mask-area-readout]').textContent();
		await drag(page, [[0.05, 0.85]]);
		expect(await maskAlpha(page, 0.05, 0.85)).toBe(0);
		await drag(page, [[0.1, 0.8], [0.3, 0.8], [0.3, 0.95]], false);
		await expect(page.locator('[data-lasso-outline]')).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(page.locator('[data-lasso-outline]')).toHaveCount(0);
		await page.mouse.up();
		expect(await maskAlpha(page, 0.2, 0.82)).toBe(0);
		await expect(options(page).locator('[data-mask-area-readout]')).toHaveText(before ?? '');

		await expect(page.locator('[data-options-apply]')).toHaveCount(0);
		expect(log.uploads).toBe(0);

		await page.locator('[data-frame-save-menu]').click();
		const send = page.locator('[data-save-row="send"]');
		await expect(send).toContainText('MASK');
		await send.click();
		await expect(bookTab(page, 'Portrait study')).toHaveClass(/active/);
		await expect.poll(() => log.uploads).toBe(2);
		await expect(page.locator('[data-field-name="image"]').first().locator('img[src*="sent-2.png"]').first()).toBeVisible();
	});

	test('the outline and the marked pixels follow a zoomed picture', async ({ page }) => {
		await start(page, true);
		await openDusk(page);
		await cropSquare(page);
		await selectRailTool(page, 'edit_region');
		await options(page).getByRole('button', { name: 'Lasso', exact: true }).click();

		await page.getByRole('button', { name: 'Zoom in' }).click();
		await page.getByRole('button', { name: 'Zoom in' }).click();
		await drag(page, [[0.42, 0.42], [0.58, 0.42], [0.58, 0.58], [0.42, 0.58]]);
		await expect.poll(() => maskAlpha(page, 0.5, 0.5)).toBe(255);
		expect(await maskAlpha(page, 0.35, 0.5)).toBe(0);
		expect(await maskAlpha(page, 0.65, 0.5)).toBe(0);
	});
});

test.describe('the lasso on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('docks Brush and Lasso above the tool strip', async ({ page }) => {
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
		await phone.locator('[data-tool-rail="strip"] [data-group-button="generate"]').click();
		await page.locator('[data-rail-sheet="generate"] [data-sheet-item="edit_region"]').click();
		await expect(page.locator('[data-rail-sheet]')).toHaveCount(0);
		const presets = page.locator('[role="dialog"][aria-label="Choose preset"]');
		await presets.waitFor({ state: 'visible', timeout: 4000 }).catch(() => undefined);
		if (await presets.isVisible()) await page.keyboard.press('Escape');
		await expect(page.locator('[data-frame-sheet]')).toHaveCount(0);
		await expect(phone.locator('[data-mask-layer]')).toBeVisible();
		await expect(phone.locator('[data-task-mark-bar]')).toBeVisible();
		await screenshot(page, JOURNEY, '10b-mask-brush-390');

		await phone.locator('[data-task-mark-bar]').getByRole('button', { name: 'Lasso', exact: true }).click();
		await expect(phone.locator('[data-task-mark-bar]').getByRole('group', { name: 'Lasso mode' })).toBeVisible();
		await drag(page, [[0.55, 0.3], [0.8, 0.35], [0.82, 0.6], [0.6, 0.7]]);
		await expect(phone.locator('[data-mask-area]')).toBeVisible();
		await expect.poll(() => maskAlpha(page, 0.7, 0.5)).toBe(255);
		await screenshot(page, JOURNEY, '10-mask-lasso-390');

		const overflowX = await page.evaluate(() => document.scrollingElement!.scrollWidth - document.scrollingElement!.clientWidth);
		expect(overflowX).toBeLessThanOrEqual(0);
	});
});
