import { test, expect, type Page, type Route } from '@playwright/test';
import { clearEdits, editorMenuItem, loginAsOwner, ownerToken, scopedStorageKey, screenshot, selectRailTool } from './helpers';
import { solidPng } from './mediaToolMocks';

const JOURNEY = 'image-editor-paint';
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
		rows.map((row) => `${row.getAttribute('data-status')}${row.hasAttribute('data-hidden') ? ':hidden' : ''}:${row.textContent?.replace(/\s+/g, '')}`).reverse()
	);
}

const ORIGINAL = 'original:00Original640×480·PNG';
const CROP = '01Crop1:1·480×480';
const REEL = '02ReelAppliestotheoriginal';

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


const CANVAS = '[data-paint-stage] canvas[aria-label="Drawing canvas"]';
const entry = (page: Page, id: string) => rail(page, `paint.${id}`);
const layerRows = (page: Page) => page.locator('[data-paint-layers] [data-layer-row]');

async function openPaint(page: Page, tool = 'brush') {
	await entry(page, tool).click();
	await expect(page.locator(CANVAS)).toBeVisible();
	await expect(page.locator(`[data-paint-tool-panel="${tool}"]`)).toBeVisible();
}

async function box(page: Page) {
	return (await page.locator(CANVAS).boundingBox())!;
}

async function stroke(page: Page, dx = -80, dy = 0) {
	const area = await box(page);
	const cx = area.x + area.width / 2;
	const cy = area.y + area.height / 2;
	await page.mouse.move(cx + dx, cy + dy);
	await page.mouse.down();
	for (let i = 1; i <= 8; i++) await page.mouse.move(cx + dx + i * 20, cy + dy + i * 6);
	await page.mouse.up();
}

async function dragRect(page: Page, from: [number, number], to: [number, number]) {
	const area = await box(page);
	const cx = area.x + area.width / 2;
	const cy = area.y + area.height / 2;
	await page.mouse.move(cx + from[0], cy + from[1]);
	await page.mouse.down();
	await page.mouse.move(cx + to[0], cy + to[1], { steps: 6 });
	await page.mouse.up();
}

async function cutAndFloat(page: Page) {
	await openPaint(page, 'brush');
	await stroke(page);
	await openPaint(page, 'rect');
	await dragRect(page, [-40, -40], [60, 40]);
	await page.locator('[data-paint-selection]').getByRole('button', { name: 'Cut', exact: true }).click();
	await page.locator('[data-paint-selection]').getByRole('button', { name: 'Paste', exact: true }).click();
	await expect(page.locator('[data-floating-row]')).toBeVisible();
}

async function layerMenu(page: Page, index: number, item: string) {
	await page.locator(`[data-layer-row="${index}"] button[aria-label^="Actions for"]`).click();
	await page.locator('[data-layer-menu]').getByRole('menuitem', { name: item, exact: true }).click();
}

test.describe('Paint entries in the Image editor', () => {
	test.use({ viewport: { width: 1440, height: 1200 } });

	test('a brush stroke lands as one step and moving among paint entries keeps the session', async ({ page }) => {
		const log = await start(page);
		await openDusk(page);
		await openPaint(page, 'brush');
		await expect(page.locator('[data-paint-tools]')).toHaveCount(0);
		await expect(page.locator('[data-paint-tool-panel="brush"] [data-field-name]')).toHaveCount(2);
		await expect(page.locator('[data-edit-stack] [data-stack-group] [data-paint-layers] [data-layer-row]')).toHaveCount(2);
		await expect(page.locator('[data-options-panel] [data-paint-layers]')).toHaveCount(0);
		await expect(page.locator('[data-stack-group] [data-stack-state="in-progress"]')).toContainText('In progress');
		await stroke(page);
		await expect(page.locator('[data-frame-undo]')).toBeEnabled();
		await screenshot(page, JOURNEY, '01-brush-1440');

		await selectRailTool(page, 'paint.eraser');
		await expect(page.locator('[data-paint-tool-panel="eraser"] [data-field-name]')).toHaveCount(1);
		await expect(page.locator('[data-paint-colour]')).toHaveCount(0);
		await screenshot(page, JOURNEY, '03-eraser-1440');
		await selectRailTool(page, 'paint.fill');
		await selectRailTool(page, 'paint.pick');
		await expect(page.locator('[data-paint-colour]')).toBeVisible();
		await selectRailTool(page, 'paint.brush');
		await expect(page.locator('[data-frame-undo]')).toBeEnabled();
		await expect(layerRows(page)).toHaveCount(2);

		await page.locator('[data-options-apply]').click();
		await expect.poll(() => stackRows(page)).toEqual([ORIGINAL, 'current:01Paint·1layer640×480100%']);
		expect(log.artifacts).toBe(1);
	});

	test('leaving Paint for another entry applies the strokes as one step', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await openPaint(page, 'brush');
		await stroke(page);
		await selectRailTool(page, 'effects');
		await expect.poll(() => stackRows(page)).toEqual([ORIGINAL, expect.stringContaining('Paint')]);
	});

	test('cut, move three times, one floating row, Enter merges and keeps the layer count', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await cutAndFloat(page);
		await openPaint(page, 'move');
		const before = await layerRows(page).count();
		const area = await box(page);
		for (let i = 0; i < 3; i++) {
			await page.mouse.move(area.x + 250 + i * 30, area.y + 190);
			await page.mouse.down();
			await page.mouse.move(area.x + 280 + i * 30, area.y + 190, { steps: 4 });
			await page.mouse.up();
		}
		await expect(page.locator('[data-floating-row]')).toHaveCount(1);
		await expect(layerRows(page)).toHaveCount(before);
		await screenshot(page, JOURNEY, '04-select-move-floating-piece-1440');
		await page.keyboard.press('Enter');
		await expect(page.locator('[data-floating-row]')).toHaveCount(0);
		await expect(layerRows(page)).toHaveCount(before);
		await expect(page.locator('[data-layer-count]')).toHaveText(String(before));
	});

	test('Keep as a new layer leaves the piece as a layer', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await cutAndFloat(page);
		await openPaint(page, 'move');
		const before = await layerRows(page).count();
		await page.locator('[data-floating-row] button[aria-label="Floating piece actions"]').click();
		await page.locator('[data-floating-menu]').getByRole('menuitem', { name: 'Keep as a new layer', exact: true }).click();
		await expect(page.locator('[data-floating-row]')).toHaveCount(0);
		await expect(layerRows(page)).toHaveCount(before + 1);
	});

	test('switching to another paint tool places the piece', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await cutAndFloat(page);
		await selectRailTool(page, 'paint.brush');
		await expect(page.locator('[data-floating-row]')).toHaveCount(0);
	});

	test('layer menu actions and the colour picker', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await openPaint(page, 'brush');
		const steps = await page.locator('[data-stack-count]').textContent();
		await page.locator('[data-stack-group] button[aria-label="Add a layer"]').click();
		await page.locator('[role="menuitem"]', { hasText: 'New layer' }).click();
		await expect(layerRows(page)).toHaveCount(3);
		await expect(page.locator('[data-stack-count]')).toHaveText(steps ?? '');
		await layerMenu(page, 2, 'Rename');
		await page.locator('input[aria-label="Layer name"]').fill('Sky');
		await page.keyboard.press('Enter');
		await expect(page.locator('[data-layer-row="2"]')).toContainText('Sky');
		await layerMenu(page, 2, 'Duplicate');
		await expect(layerRows(page)).toHaveCount(4);
		await layerMenu(page, 3, 'Merge down');
		await expect(layerRows(page)).toHaveCount(3);
		await layerMenu(page, 2, 'Delete');
		await expect(layerRows(page)).toHaveCount(2);
		await screenshot(page, JOURNEY, '05-layer-menu-1440');

		await page.locator('[data-options-panel] [data-colour-swatch]').click();
		const picker = page.locator('[data-colour-picker]');
		await expect(picker).toBeVisible();
		await screenshot(page, JOURNEY, '06-colour-picker-1440');
		await picker.locator('[aria-label="Use colour #e5484d"]').click();
		await expect(page.locator('[data-options-panel] [data-colour-hex]')).toHaveText('#e5484d');
		await page.keyboard.press('Escape');
		await expect(picker).toHaveCount(0);
		await expect(page.locator('[data-image-editor]')).toBeVisible();
	});

	test('tool letters select the paint entries and not while typing', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await page.keyboard.press('e');
		await expect(page.locator('[data-paint-tool-panel="eraser"]')).toBeVisible();
		await page.keyboard.press('g');
		await expect(page.locator('[data-paint-tool-panel="fill"]')).toBeVisible();
		await page.keyboard.press('v');
		await expect(page.locator('[data-paint-tool-panel="move"]')).toBeVisible();
		await page.keyboard.press('m');
		await expect(page.locator('[data-paint-tool-panel="rect"]')).toBeVisible();
		await page.keyboard.press('b');
		await page.locator('[data-layer-row="1"] [data-layer-select]').dblclick();
		await page.locator('input[aria-label="Layer name"]').press('e');
		await expect(page.locator('[data-paint-tool-panel="brush"]')).toBeVisible();
		await page.keyboard.press('Escape');
	});

	test('Edit menu has the paint actions area and Tools menu shows the letters', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await openPaint(page, 'brush');
		const row = await editorMenuItem(page, 'Tools', 'Paint');
		await row.hover();
		await expect(page.locator('[data-editor-submenu] [data-menu-keybind]').first()).toBeVisible();
		await screenshot(page, JOURNEY, '08c-tools-menu-1440');
		await page.keyboard.press('Escape');
	});

	test('reopening an applied paint step opens it as one layer over the original', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await openPaint(page, 'brush');
		await stroke(page);
		await page.locator('[data-options-apply]').click();
		await expect.poll(() => stackRows(page)).toEqual([ORIGINAL, 'current:01Paint·1layer640×480100%']);
		await page.locator('[data-edit-stack="panel"] [data-stack-row]').nth(0).click();
		await expect(page.locator(CANVAS)).toBeVisible();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', /paint\./);
		await expect(layerRows(page)).toHaveCount(2);
	});
});

test.describe('the AI ring on the rail', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('Generate is the first group; the ring rests on its entries and spins when one is selected', async ({ page }) => {
		await start(page);
		await openDusk(page);
		const groups = await page.locator('[data-tool-rail="icons"] button[data-tool]').evaluateAll((nodes) => nodes.map((node) => (node as HTMLElement).dataset.tool));
		expect(groups.slice(0, 2)).toEqual(['edit_region', 'remove_object']);
		await expect(rail(page, 'edit_region')).toHaveClass(/ai-ring(\s|$)/);
		await expect(rail(page, 'paint.brush')).not.toHaveClass(/ai-ring/);
		await screenshot(page, JOURNEY, 'rail-ai-rest-1440');
		await selectRailTool(page, 'edit_region');
		await expect(rail(page, 'edit_region')).toHaveClass(/ai-ring--on/);
		await screenshot(page, JOURNEY, 'rail-ai-selected-1440');
	});
});

test.describe('the rail with Paint groups', () => {
	test.use({ viewport: { width: 1440, height: 700 } });

	test('at a short height the Paint groups fold after Effects, Cut out and Select and move and never scroll', async ({ page }) => {
		await start(page);
		await openDusk(page);
		const railEl = page.locator('[data-tool-rail="icons"]');
		const folded = async () => ((await railEl.getAttribute('data-rail-folded')) ?? '').split(' ').filter(Boolean);
		const overflow = () => railEl.evaluate((node) => node.scrollHeight - node.clientHeight);
		expect(await overflow()).toBeLessThanOrEqual(1);
		for (const height of [900, 800, 700, 640]) {
			await page.setViewportSize({ width: 1440, height });
			await expect.poll(overflow).toBeLessThanOrEqual(1);
			const now = await folded();
			if (now.includes('paint')) expect(now).toEqual(expect.arrayContaining(['fx', 'tools', 'select', 'look']));
			if (now.includes('select')) expect(now).toEqual(expect.arrayContaining(['fx', 'tools']));
			expect(now).not.toContain('generate');
		}
		await page.setViewportSize({ width: 1440, height: 768 });
		await expect.poll(folded).toEqual(['fx', 'tools', 'select', 'look', 'paint']);
		await expect(page.locator('[data-tool-rail="icons"] button[data-group-button="paint"]')).toBeVisible();
		await page.locator('[data-tool-rail="icons"] button[data-group-button="select"]').hover();
		await expect(page.locator('[data-rail-flyout="select"]')).toBeVisible();
		await screenshot(page, JOURNEY, '02-paint-flyout-1440');
	});
});

test.describe('Paint on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('paint entries go through the group sheet and the layers sit in the Edits sheet', async ({ page }) => {
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
		await expect(phone.locator('[data-tool-rail="strip"]')).toBeVisible();
		await phone.locator('[data-tool-rail="strip"] button[data-group-button="paint"]').click();
		await expect(page.locator('[data-rail-sheet="paint"]')).toBeVisible();
		await screenshot(page, JOURNEY, '10b-phone-paint-group-sheet-390');
		await page.locator('[data-rail-sheet="paint"] [data-sheet-item="paint.brush"]').click();
		await expect(phone.locator('[data-paint-tool-panel="brush"]')).toBeVisible();
		await screenshot(page, JOURNEY, '10-phone-brush-390');
		await expect(phone.locator('[data-paint-layers-button]')).toHaveCount(0);
		await expect(phone.locator('[data-paint-options="phone"] [data-paint-layers]')).toHaveCount(0);
		await phone.locator('[data-frame-edits]').click();
		await expect(page.getByRole('dialog', { name: 'Edits' }).locator('[data-stack-group] [data-layer-row]')).toHaveCount(2);
		await screenshot(page, JOURNEY, '11-phone-layers-390');
	});
});

test.describe('Edits as layers', () => {
	test.use({ viewport: { width: 1440, height: 1100 } });

	const row = (page: Page, id: string) => page.locator(`[data-edit-stack="panel"] [data-stack-row="${id}"]`);
	const contrast = (page: Page) => page.locator('[data-field-name="tone_contrast"] input[type="range"]');

	async function paintedLayer(page: Page) {
		await start(page);
		await openDusk(page);
		await openPaint(page, 'brush');
		await stroke(page);
		await page.locator('[data-options-apply]').click();
		await expect(row(page, 's1')).toHaveAttribute('data-stack-kind', 'layer');
	}

	async function addLook(page: Page) {
		await selectRailTool(page, 'light');
		await contrast(page).fill('20');
		await contrast(page).dispatchEvent('change');
		await expect(row(page, 's2')).toBeVisible();
	}

	test('the newest edit is on top and the paint step is a layer row with its opacity and eye', async ({ page }) => {
		await paintedLayer(page);
		await addLook(page);
		const keys = await page.locator('[data-edit-stack="panel"] [data-stack-row]').evaluateAll((rows) => rows.map((node) => node.getAttribute('data-stack-row')));
		expect(keys).toEqual(['s2', 's1', 'original']);
		await expect(row(page, 's1').locator('[data-stack-opacity]')).toHaveText('100%');
		await expect(row(page, 's1').locator('[data-stack-hide]')).toBeVisible();
		await expect(row(page, 's2')).toHaveAttribute('data-stack-kind', 'slim');
		await expect(row(page, 's2')).toContainText('Applies to 2 layers below');
	});

	test('a layer fades from the drawer and the fade is undoable', async ({ page }) => {
		await paintedLayer(page);
		await row(page, 's1').locator('[data-stack-open]').click();
		const drawer = page.locator('[data-layer-drawer="s1"]');
		await expect(drawer).toBeVisible();
		const slider = drawer.locator('input[type="range"]');
		await slider.fill('50');
		await slider.dispatchEvent('change');
		await expect(row(page, 's1').locator('[data-stack-opacity]')).toHaveText('50%');
		await selectRailTool(page, 'light');
		const readout = row(page, 's1').locator('[data-stack-opacity]');
		for (let i = 0; i < 2 && (await readout.textContent()) !== '100%'; i++) await page.locator('[data-frame-undo]').click();
		await expect(readout).toHaveText('100%');
		await expect(row(page, 's1')).toBeVisible();
	});

	test('hiding a layer is undoable', async ({ page }) => {
		await paintedLayer(page);
		await selectRailTool(page, 'light');
		await row(page, 's1').locator('[data-stack-hide]').click();
		await expect(row(page, 's1')).toHaveAttribute('data-hidden', 'true');
		await page.locator('[data-frame-undo]').click();
		await expect(row(page, 's1')).not.toHaveAttribute('data-hidden', 'true');
	});

	test('a selected look brackets the layers it applies to', async ({ page }) => {
		await paintedLayer(page);
		await row(page, 's1').locator('[data-stack-open]').click();
		const slider = page.locator('[data-layer-drawer="s1"] input[type="range"]');
		await slider.fill('50');
		await slider.dispatchEvent('change');
		await addLook(page);
		await row(page, 's2').locator('[data-stack-open]').click();
		for (const id of ['s2', 's1', 'original']) await expect(row(page, id)).toHaveAttribute('data-bracket', 'true');
		await screenshot(page, JOURNEY, '20-cmb407-built-1440');
	});
});
