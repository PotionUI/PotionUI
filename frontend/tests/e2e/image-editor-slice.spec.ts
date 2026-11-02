import { test, expect, type Locator, type Page, type Route } from '@playwright/test';
import { expectNoBoxOverlap, loginAsOwner, ownerToken, scopedStorageKey, screenshot } from './helpers';
import { HEIGHT, WIDTH, sheetPng } from './sliceSheet';

const JOURNEY = 'image-editor-slice';
const GENERATION_ID = 'gen-e2e-sheet';
const FILE_ID = 701;
const SOURCE_FILE = `outputs/2026-11-02/${GENERATION_ID}/mira-sheet.png`;

type Json = Record<string, unknown>;

const SHEET = sheetPng();
const SHEET_URL = `data:image/png;base64,${SHEET.toString('base64')}`;

const FORM_SCHEMA = {
	properties: {
		generation: {
			type: 'group',
			title: 'Generation',
			children: [
				{ name: 'prompt', type: 'string', title: 'Prompt', default: 'mira walking' },
				{ name: 'refs', type: 'image', title: 'References', multiple: true, max_items: 8 }
			]
		}
	}
};

interface Log {
	runs: Json[];
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
		form_data: { prompt: 'a character sheet' },
		status: 'completed',
		progress: 1,
		created_at: now,
		updated_at: now,
		completed_at: now,
		rating: 0,
		is_favorite: false,
		files: [
			{
				id: FILE_ID,
				file_path: SOURCE_FILE,
				file_type: 'image',
				is_final: true,
				created_at: now,
				width: WIDTH,
				height: HEIGHT,
				thumbnail_small: SHEET_URL,
				thumbnail_medium: SHEET_URL,
				thumbnail_large: SHEET_URL
			}
		]
	};
}

function item(n: number): Json {
	return {
		id: `cell-${n}`,
		filename: `mira-sheet-0${n}.png`,
		original_filename: `mira-sheet-0${n}.png`,
		media_type: 'image',
		mime_type: 'image/png',
		url: `/api/e2e-art/cell-${n}.png`,
		width: 200,
		height: 200,
		size: 2048
	};
}

async function mockBackend(page: Page): Promise<Log> {
	const log: Log = { runs: [] };
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
		route.request().method() === 'GET' ? json(route, { generations: [generation()], total: 1 }) : route.fallback()
	);
	await page.route(`**/api/media/generations/${GENERATION_ID}/mira-sheet.png*`, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: SHEET }));
	await page.route('**/api/e2e-art/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: SHEET }));
	await page.route(/\/api\/collections(\?.*)?$/, (route) =>
		route.request().method() === 'GET'
			? json(route, { collections: [{ id: 'col-mira', name: 'Mira refs', scope: 'library', parent_id: null, created_at: '2026-11-01T00:00:00Z', item_count: 3 }], total: 1 })
			: route.fallback()
	);
	await page.route(/\/api\/media-tools\/runs$/, async (route) => {
		if (route.request().method() !== 'POST') return route.fallback();
		log.runs.push(route.request().postDataJSON() as Json);
		await json(route, { run_id: `run-${log.runs.length}` }, 202);
	});
	await page.route(/\/api\/media-tools\/runs\/[^/]+$/, async (route) => {
		const id = route.request().url().split('/').pop() as string;
		const body = log.runs[log.runs.length - 1] ?? {};
		const steps = (body.steps as { tool: string; params: { rects?: unknown[] } }[]) ?? [];
		const count = steps.find((step) => step.tool === 'slice')?.params.rects?.length ?? 1;
		await json(route, {
			run_id: id,
			status: 'completed',
			progress: 1,
			label: 'Slice',
			replaced: false,
			error: null,
			items: Array.from({ length: count }, (_, i) => item(i + 1))
		});
	});
	await page.route('**/api/presets/*/form*', (route) => json(route, { preset_id: 'mock', form_schema: FORM_SCHEMA }));
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
				if (sessionStorage.getItem('image-editor-slice-seeded')) return;
				sessionStorage.setItem('image-editor-slice-seeded', '1');
				const tab = {
					id: 'hero-shot',
					name: 'Hero shot',
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

async function savedTabs(page: Page): Promise<Array<{ id: string; kind?: string; tool?: string; activeTool?: string | null; formData?: Json }>> {
	const key = await scopedStorageKey(page, 'potionui_tabs_state');
	return page.evaluate((storageKey) => JSON.parse(localStorage.getItem(storageKey) ?? '{"tabs":[]}').tabs, key);
}

const cells = (page: Page) => page.locator('[data-slice-overlay] [data-slice-cell]');
const kept = (page: Page) => page.locator('[data-slice-overlay] [data-slice-cell]:not([data-slice-cell="skipped"])');
async function lightShot(page: Page, label: string) {
	await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
	await screenshot(page, JOURNEY, label);
	await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));
}

async function badgesClear(page: Page) {
	await expectNoBoxOverlap([
		{ name: 'canvas badge', locator: page.locator('[data-canvas-art] > .pointer-events-none.absolute.left-3.top-3 > *') },
		{ name: 'cell 01 number', locator: page.locator('[data-slice-cell="01"] > span') }
	]);
}

const rail = (page: Page, tool: string) => page.locator(`[data-tool-rail] button[data-tool="${tool}"]`);

async function openSheetInEditor(page: Page) {
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
	const empty = page.getByTestId('tool-tab-empty');
	await expect(empty.locator('[data-recent-history] button')).toHaveCount(1);
	await empty.locator('[data-recent-history] button').first().click();
	await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop');
	await rail(page, 'slice').click();
	await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'slice');
	await expect(page.locator('[data-slice-detect]').first()).toBeVisible();
}

async function bandLeft(band: Locator): Promise<number> {
	return band.evaluate((el) => parseFloat((el as HTMLElement).style.left));
}

async function verticalBands(page: Page): Promise<number[]> {
	const bands = page.locator('[data-slice-overlay] [data-slice-band].v');
	const lefts: number[] = [];
	for (let i = 0; i < (await bands.count()); i++) lefts.push(Math.round(await bandLeft(bands.nth(i))));
	return [...new Set(lefts)].sort((a, b) => a - b);
}

test.describe('Slice on desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('a 3×2 grid snaps, Detect finds the panels, merge and skip, then save and use as references', async ({ page }) => {
		test.setTimeout(180000);
		const log = await mockBackend(page);
		await loginAsOwner(page);
		await seedGenerationTab(page);
		await page.goto('/generate');
		await expect(page.locator('.tab-bar-container')).toBeVisible({ timeout: 15000 });
		await openSheetInEditor(page);
		await expect(page.locator('[data-slice-intro]')).toBeVisible();
		await screenshot(page, JOURNEY, '01-sheet-open-1440');

		await page.locator('[data-slice-preset="3x2"]').first().click();
		await expect(cells(page)).toHaveCount(6);
		expect(await verticalBands(page)).toEqual([Math.round((300 / WIDTH) * 100), Math.round((610 / WIDTH) * 100)]);
		await expect(page.locator('[data-slice-cut]')).toHaveCount(1);
		await expect(page.locator('[data-slice-cut]')).toContainText('Cuts a panel');
		await expect(page.locator('[data-slice-cut-merge]')).toHaveText('Merge 04 + 05');
		await expect(rail(page, 'slice').locator('[data-tool-pending]')).toBeVisible();

		const knob = page.locator('[data-slice-knob="v"]').nth(1);
		const box = (await knob.boundingBox())!;
		await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
		await page.mouse.down();
		await page.mouse.move(box.x + box.width / 2 + 6, box.y + box.height / 2, { steps: 3 });
		await expect(page.locator('[data-slice-drag-chip]')).toContainText(/Gutter · \d+ px/);
		await expect(page.locator('[data-slice-magnet]')).toHaveCount(2);
		await screenshot(page, JOURNEY, '02-grid-snapped-1440');
		await lightShot(page, '02-grid-snapped-light-1440');
		await page.mouse.up();
		await badgesClear(page);
		await expect(page.locator('[data-editor-canvas]').getByText('Preview', { exact: true })).toHaveCount(0);
		expect(await verticalBands(page)).toEqual([Math.round((300 / WIDTH) * 100), Math.round((610 / WIDTH) * 100)]);

		await page.locator('[data-slice-detect]').first().click();
		await expect(cells(page)).toHaveCount(6);
		await expect(page.locator('[data-slice-detected]')).toContainText('6 panels found');
		await expect(page.locator('[data-slice-cut]')).toHaveCount(0);
		await screenshot(page, JOURNEY, '03-detect-panels-1440');

		await page.locator('[data-slice-cell="05"]').click();
		await page.locator('[data-slice-cell="06"]').click({ modifiers: ['Shift'] });
		await page.locator('[data-cell-action="merge"]').click();
		await expect(cells(page)).toHaveCount(5);
		await page.keyboard.press('Control+z');
		await expect(cells(page)).toHaveCount(6);
		await page.keyboard.press('Control+Shift+z');
		await expect(cells(page)).toHaveCount(5);

		await page.locator('[data-slice-cell="03"]').click();
		await page.locator('[data-cell-action="skip"]').click();
		await expect(kept(page)).toHaveCount(4);
		await expect(page.locator('[data-slice-cell="skipped"]')).toHaveCount(1);

		await page.locator('[data-slice-tab="output"]').click();
		await expect(page.locator('[data-slice-name-row]')).toHaveCount(5);
		await expect(page.locator('[data-slice-name-row="01"] input')).toHaveValue('mira-sheet-01');
		await expect(page.locator('[data-slice-strip] [data-strip-item]')).toHaveCount(4);
		await expect(page.locator('[data-slice-strip] [data-strip-skipped]')).toHaveCount(1);
		await screenshot(page, JOURNEY, '04-cell-options-1440');

		const primary = page.locator('[data-frame-save]');
		await expect(primary).toHaveText('Save 4 images');
		await page.locator('[data-frame-save-menu]').click();
		const menu = page.getByRole('menu', { name: 'More save options' });
		await expect(menu.locator('[data-save-row]')).toHaveCount(6);
		await expect(menu.locator('[data-save-row="new-generation"]')).toContainText('Pick a preset with a references field');
		await expect(menu.locator('[data-save-row="replace"]')).toBeDisabled();
		await expect(menu.locator('[data-save-row="references"]')).toContainText('Hero shot');
		await expect(menu.locator('[data-save-row="references"]')).toContainText('8 of 8 slots free');
		await screenshot(page, JOURNEY, '05b-save-menu-1440');
		await lightShot(page, '05b-save-menu-light-1440');
		await page.keyboard.press('Escape');

		await primary.click();
		await expect.poll(() => log.runs.length).toBe(1);
		const body = log.runs[0] as { steps: { tool: string; params: Json }[]; output: Json };
		expect(body.output).toEqual({ mode: 'new' });
		expect(body.steps[body.steps.length - 1].tool).toBe('slice');
		const params = body.steps[body.steps.length - 1].params as { rects: { x: number; y: number; w: number; h: number; name: string }[]; fill: string };
		expect(params.rects).toHaveLength(4);
		for (const rect of params.rects) {
			for (const value of [rect.x, rect.y, rect.w, rect.h]) expect(value).toBeGreaterThanOrEqual(0);
			expect(rect.x + rect.w).toBeLessThanOrEqual(1.000001);
			expect(rect.y + rect.h).toBeLessThanOrEqual(1.000001);
		}
		expect(params.rects.map((rect) => rect.name)).toEqual(['mira-sheet-01', 'mira-sheet-02', 'mira-sheet-03', 'mira-sheet-04']);
		expect(params.rects[0].x).toBeCloseTo(24 / WIDTH, 2);
		expect(params.fill).toBe('auto');

		const toast = page.locator('[role="status"]').filter({ hasText: 'Saved 4 images to Library' });
		await expect(toast).toBeVisible();
		await expect(toast.locator('[data-toast-action]')).toHaveText(['Open in Library', 'Use as references']);
		await expect(primary).toHaveText('Saved');
		await screenshot(page, JOURNEY, '07-done-1440');

		await toast.getByRole('button', { name: 'Use as references' }).click();
		await expect.poll(async () => ((await savedTabs(page)).find((tab) => tab.id === 'hero-shot')?.formData?.refs as unknown[] | undefined)?.length ?? 0).toBe(4);
		await expect(page.locator('button.book-tab.active, button.book-tab[aria-selected="true"]').first()).toContainText('Hero shot');
	});
});

test.describe('Slice from History', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('Slice into references opens the editor on Slice with the panels detected', async ({ page }) => {
		await mockBackend(page);
		await loginAsOwner(page);
		await page.goto('/history');
		const card = page.locator(`[data-generation-card][data-generation-id="${GENERATION_ID}"]`);
		await expect(card).toBeVisible();
		await card.locator('.media-zoom').click();
		const bar = page.getByRole('toolbar', { name: 'Actions' });
		await expect(bar).toBeVisible();
		await bar.getByRole('button', { name: 'Edit' }).click();
		const menu = page.getByRole('menu', { name: 'Edit' });
		const row = menu.getByRole('menuitem', { name: 'Slice into references', exact: true });
		await expect(row).toBeVisible();
		await screenshot(page, JOURNEY, '09-entry-points-1440');
		await row.click();

		await expect(page).toHaveURL(/\/generate/);
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'slice');
		await expect(cells(page)).toHaveCount(6, { timeout: 15000 });
		await expect(page.locator('[data-slice-detected]')).toContainText('6 panels found');
		await expect.poll(async () => (await savedTabs(page)).filter((tab) => tab.kind === 'tool').map((tab) => [tab.tool, tab.activeTool])).toEqual([['image', 'slice']]);
	});
});

test.describe('Slice from E', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('E on a sheet offers a quiet Slice chip that opens Slice with the panels', async ({ page }) => {
		await mockBackend(page);
		await loginAsOwner(page);
		await page.goto('/history');
		const card = page.locator(`[data-generation-card][data-generation-id="${GENERATION_ID}"]`);
		await card.locator('.media-zoom').click();
		await expect(page.getByRole('toolbar', { name: 'Actions' })).toBeVisible();
		await page.keyboard.press('e');
		await expect(page).toHaveURL(/\/generate/);
		const chip = page.locator('[data-slice-suggest]');
		await expect(chip).toContainText('Slice 6 panels', { timeout: 15000 });
		await screenshot(page, JOURNEY, '09-e-chip-1440');
		await chip.getByRole('button', { name: 'Slice 6 panels' }).click();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'slice');
		await expect(cells(page)).toHaveCount(6, { timeout: 15000 });
		await expect(chip).toHaveCount(0);
	});

	test('the Slice chip can be dismissed and stays dismissed for that picture', async ({ page }) => {
		await mockBackend(page);
		await loginAsOwner(page);
		await page.goto('/history');
		await page.locator(`[data-generation-card][data-generation-id="${GENERATION_ID}"]`).locator('.media-zoom').click();
		await expect(page.getByRole('toolbar', { name: 'Actions' })).toBeVisible();
		await page.keyboard.press('e');
		const chip = page.locator('[data-slice-suggest]');
		await expect(chip).toBeVisible({ timeout: 15000 });
		await chip.getByRole('button', { name: 'Dismiss' }).click();
		await expect(chip).toHaveCount(0);
		const dismissed = await page.evaluate(() => JSON.parse(localStorage.getItem('potionui_slice_hint_dismissed') ?? '[]'));
		expect(dismissed).toHaveLength(1);
		await page.locator('a[href="/history"]').first().click();
		await page.locator(`[data-generation-card][data-generation-id="${GENERATION_ID}"]`).locator('.media-zoom').click();
		await expect(page.getByRole('toolbar', { name: 'Actions' })).toBeVisible();
		await page.keyboard.press('e');
		await expect(page.locator('[data-image-editor]')).toBeVisible({ timeout: 15000 });
		await page.waitForLoadState('networkidle');
		await page.waitForTimeout(800);
		await expect(chip).toHaveCount(0);
	});
});

test.describe('Slice on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

	test('the dock shows Detect and the grids, lines get touch handles, and a tapped cell swaps the dock', async ({ page }) => {
		await mockBackend(page);
		await loginAsOwner(page);
		await page.goto('/history');
		const card = page.locator(`[data-generation-card][data-generation-id="${GENERATION_ID}"]`);
		await expect(card).toBeVisible();
		await card.locator('.media-zoom').click();
		const bar = page.getByRole('toolbar', { name: 'Actions' });
		await bar.getByRole('button', { name: 'Edit' }).click();
		await page.getByRole('menuitem', { name: 'Slice into references', exact: true }).click();

		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'slice');
		await expect(cells(page)).toHaveCount(6, { timeout: 15000 });
		await expect(page.locator('[data-slice-phone-layout]')).toBeVisible();
		await expect(page.locator('[data-slice-kept]')).toHaveText('6 of 6 kept');
		const knob = page.locator('[data-slice-knob]').first();
		const size = (await knob.boundingBox())!;
		expect(Math.round(size.width)).toBe(44);
		expect(Math.round(size.height)).toBe(44);
		await expect(page.locator('[data-slice-strip]')).toBeVisible();
		const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
		expect(overflow).toBeLessThanOrEqual(0);
		await screenshot(page, JOURNEY, '08-phone-390');

		await page.locator('[data-slice-cell="04"]').tap();
		await expect(page.locator('[data-slice-phone-cell]')).toContainText('Cell 04');
		await screenshot(page, JOURNEY, '08-phone-cell-390');
	});
});
