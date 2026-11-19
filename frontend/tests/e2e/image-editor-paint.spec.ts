import { test, expect, type Page, type Route } from '@playwright/test';
import { clearEdits, editorMenuItem, loginAsOwner, ownerToken, scopedStorageKey, screenshot, selectRailTool, railButton } from './helpers';
import { colourPng, solidPng } from './mediaToolMocks';

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
	pngs: Map<string, Buffer>;
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
	const log: Log = { runs: [], artifacts: 0, uploads: 0, frames: [], pngs: new Map() };
	const source = solidPng(640, 480);
	const small = solidPng(64, 64);

	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
		route.request().method() === 'GET' ? json(route, { generations: [generation()], total: 1 }) : route.fallback()
	);
	await page.route(`**/api/media/generations/${GENERATION_ID}/observatory-dusk.png*`, (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: source })
	);
	await page.route('**/api/e2e-art/**', (route) => {
		const name = new URL(route.request().url()).pathname.split('/').pop()!.replace(/\.png$/, '');
		return route.fulfill({ status: 200, contentType: 'image/png', body: log.pngs.get(name) ?? small });
	});
	await page.route(/\/api\/media-tools\/artifacts$/, async (route) => {
		log.artifacts += 1;
		const body = route.request().postDataBuffer();
		const start = body ? body.indexOf(Buffer.from([0x89, 0x50, 0x4e, 0x47])) : -1;
		const end = body && start >= 0 ? body.indexOf(Buffer.from('IEND'), start) : -1;
		if (body && start >= 0 && end > start) log.pngs.set(`art-${log.artifacts}`, body.subarray(start, end + 8));
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

const CANVAS = '[data-paint-stage] canvas[aria-label="Drawing canvas"]';
const panel = (page: Page) => page.locator('[data-edit-stack="panel"]');
const layerRows = (page: Page) => panel(page).locator('[data-stack-section="layers"] [data-stack-kind="layer"]');
const layerOrder = (page: Page) => layerRows(page).evaluateAll((rows) => rows.map((node) => node.getAttribute('data-stack-row')).reverse());

async function ready(page: Page) {
	await expect(page.locator('[data-paint-stage][data-paint-ready]')).toBeVisible();
}

async function addPaintLayer(page: Page) {
	await page.locator('[data-layers-add]').click();
	await page.locator('[data-layers-add-item="new"]').click();
	await ready(page);
}

async function openPaint(page: Page, tool = 'brush') {
	await selectRailTool(page, `paint.${tool}`);
	await expect(page.locator(CANVAS)).toBeVisible();
	await ready(page);
	await expect(page.locator(`[data-paint-tool-panel="${tool}"]`)).toBeVisible();
}

async function box(page: Page) {
	await ready(page);
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

function pixel(page: Page, dx: number, dy: number) {
	return page.locator(CANVAS).evaluate(
		(canvas: HTMLCanvasElement, [x, y]) => {
			const rect = canvas.getBoundingClientRect();
			const scale = canvas.width / rect.width;
			const data = canvas.getContext('2d')!.getImageData(Math.round((rect.width / 2 + x) * scale), Math.round((rect.height / 2 + y) * scale), 1, 1).data;
			return Array.from(data).join(',');
		},
		[dx, dy]
	);
}

async function painted(page: Page, dx: number, dy: number) {
	await ready(page);
	const [red, green, blue] = (await pixel(page, dx + 80, dy + 24)).split(',').map(Number);
	return red < 60 && green < 60 && blue < 60;
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

test.describe('Paint entries in the Image editor', () => {
	test.use({ viewport: { width: 1440, height: 1200 } });

	test('the first stroke makes one layer at once and the paint entries keep painting on it', async ({ page }) => {
		const log = await start(page);
		await openDusk(page);
		await openPaint(page, 'brush');
		await expect(page.locator('[data-paint-tools]')).toHaveCount(0);
		await expect(page.locator('[data-paint-tool-panel="brush"] [data-field-name]')).toHaveCount(2);
		await expect(layerRows(page)).toHaveCount(0);
		await expect(page.locator('[data-paint-target="new"]')).toContainText('Painting on a new layer');
		await expect(page.locator('[data-paint-layers], [data-paint-group], [data-stack-group], [data-layer-row], [data-stack-live]')).toHaveCount(0);
		await stroke(page);
		await expect.poll(() => stackRows(page)).toEqual([ORIGINAL, 'current:Paint1Paintlayer100%']);
		await expect(page.locator('[data-paint-target="layer"]')).toContainText('Painting on Paint 1');
		await expect(page.locator('[data-options-apply], [data-options-reset]')).toHaveCount(0);
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
		await stroke(page, -80, 80);
		await expect(layerRows(page)).toHaveCount(1);
		await expect.poll(() => log.artifacts).toBeGreaterThanOrEqual(1);
	});

	test('leaving Paint for another entry changes nothing in the layers', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await openPaint(page, 'brush');
		await stroke(page);
		await expect(layerRows(page)).toHaveCount(1);
		await selectRailTool(page, 'effects');
		await expect.poll(() => stackRows(page)).toEqual([ORIGINAL, expect.stringContaining('Paint1')]);
		await openPaint(page, 'brush');
		await expect(layerRows(page)).toHaveCount(1);
		await expect(page.locator('[data-paint-target="layer"]')).toContainText('Painting on Paint 1');
	});

	test('cut, move three times, one floating row, Enter places it back on the same layer', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await cutAndFloat(page);
		await openPaint(page, 'move');
		const area = await box(page);
		for (let i = 0; i < 3; i++) {
			await page.mouse.move(area.x + 250 + i * 30, area.y + 190);
			await page.mouse.down();
			await page.mouse.move(area.x + 280 + i * 30, area.y + 190, { steps: 4 });
			await page.mouse.up();
		}
		await expect(page.locator('[data-floating-row]')).toHaveCount(1);
		await expect(layerRows(page)).toHaveCount(1);
		await screenshot(page, JOURNEY, '04-select-move-floating-piece-1440');
		await page.keyboard.press('Enter');
		await expect(page.locator('[data-floating-row]')).toHaveCount(0);
		await expect(layerRows(page)).toHaveCount(1);
	});

	test('Keep as a new layer makes the piece a layer row right away', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await cutAndFloat(page);
		await openPaint(page, 'move');
		await page.locator('[data-floating-keep]').click();
		await expect(page.locator('[data-floating-row]')).toHaveCount(0);
		await expect(layerRows(page)).toHaveCount(2);
	});

	test('switching to another paint tool places the piece', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await cutAndFloat(page);
		await selectRailTool(page, 'paint.brush');
		await expect(page.locator('[data-floating-row]')).toHaveCount(0);
		await expect(layerRows(page)).toHaveCount(1);
	});

	test('the Layers panel is the layer list while painting: a layer is added, selected and renamed there, and the colour picker', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await openPaint(page, 'brush');
		await stroke(page);
		await addPaintLayer(page);
		await expect(layerRows(page)).toHaveCount(2);
		await expect(panel(page).locator('[data-stack-row="s2"]')).toHaveAttribute('aria-current', 'true');
		await expect(page.locator('[data-paint-target="layer"]')).toContainText('Painting on Paint 2');
		await stroke(page, -60, 60);
		await expect(layerRows(page)).toHaveCount(2);
		await panel(page).locator('[data-stack-row="s1"] [data-stack-open]').click();
		await expect(page.locator('[data-paint-target="layer"]')).toContainText('Painting on Paint 1');
		await page.getByRole('button', { name: /^Layer options for/ }).click();
		await page.getByRole('menuitem', { name: 'Rename', exact: true }).click();
		await page.locator('input[aria-label="Layer name"]').fill('Sky');
		await page.keyboard.press('Enter');
		await expect(page.locator('[data-paint-target="layer"]')).toContainText('Painting on Sky');
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
		await stroke(page);
		await addPaintLayer(page);
		await page.locator('[data-stack-row="s1"] [data-stack-open]').click();
		await page.getByRole('button', { name: /^Layer options for/ }).click();
		await page.getByRole('menuitem', { name: 'Rename', exact: true }).click();
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

	test('clicking a paint layer from another tool opens Paint on that layer', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await openPaint(page, 'brush');
		await stroke(page);
		await selectRailTool(page, 'crop');
		await expect.poll(() => stackRows(page)).toEqual([ORIGINAL, 'current:Paint1Paintlayer100%']);
		await panel(page).locator('[data-stack-row="s1"] [data-stack-open]').click();
		await expect(page.locator(CANVAS)).toBeVisible();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', /paint\./);
		await expect(page.locator('[data-layer-drawer="s1"]')).toBeVisible();
		await expect(page.locator('[data-paint-target="layer"]')).toContainText('Painting on Paint 1');
	});
});

test.describe('Layers that exist from the moment they are added', () => {
	test.use({ viewport: { width: 1440, height: 1100 } });

	test('adding gives exactly one selected row, strokes land in it, and clicking away, switching tools or reloading creates nothing', async ({ page }) => {
		test.setTimeout(90000);
		const log = await start(page);
		await openDusk(page);
		await addPaintLayer(page);
		await expect(layerRows(page)).toHaveCount(1);
		await expect(panel(page).locator('[data-stack-row="s1"]')).toHaveAttribute('aria-current', 'true');
		await stroke(page, -80, -80);
		await addPaintLayer(page);
		await expect(layerRows(page)).toHaveCount(2);
		await expect(panel(page).locator('[data-stack-row="s2"]')).toHaveAttribute('aria-current', 'true');
		await stroke(page, -80, 60);
		await expect.poll(() => layerOrder(page)).toEqual(['s1', 's2']);
		await page.waitForTimeout(400);
		await screenshot(page, JOURNEY, '50-simple-layers-1440');

		const body = (await page.locator('[data-options-panel="desktop"] [data-options-body]').boundingBox())!;
		await page.mouse.click(body.x + 12, body.y + body.height - 12);
		await selectRailTool(page, 'crop');
		await selectRailTool(page, 'light');
		await openPaint(page, 'eraser');
		await openPaint(page, 'brush');
		await expect.poll(() => layerOrder(page)).toEqual(['s1', 's2']);
		await expect.poll(() => painted(page, -80, -80)).toBe(true);
		await expect.poll(() => painted(page, -80, 60)).toBe(true);

		await expect.poll(() => log.artifacts).toBe(2);
		await page.waitForTimeout(600);
		await page.reload();
		await expect(page.locator('[data-image-editor]')).toBeVisible({ timeout: 30000 });
		await expect(layerRows(page)).toHaveCount(2, { timeout: 20000 });
		await expect.poll(() => layerOrder(page)).toEqual(['s1', 's2']);
		await panel(page).locator('[data-stack-row="s1"] [data-stack-open]').click();
		await expect.poll(() => painted(page, -80, -80), { timeout: 15000 }).toBe(true);
		await expect.poll(() => painted(page, -80, 60), { timeout: 15000 }).toBe(true);
		await expect(layerRows(page)).toHaveCount(2);
	});

	test('a stroke with Original selected makes one new layer right above it', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await openPaint(page, 'brush');
		await stroke(page, -80, -80);
		await addPaintLayer(page);
		await stroke(page, -80, 60);
		await panel(page).locator('[data-stack-row="original"] [data-stack-open]').click();
		await expect(panel(page).locator('[data-stack-row="original"]')).toHaveAttribute('aria-current', 'true');
		await expect(page.locator('[data-paint-target="new"]')).toBeVisible();
		await expect(layerRows(page)).toHaveCount(2);
		await stroke(page, -80, 0);
		await expect.poll(() => layerOrder(page)).toEqual(['s3', 's1', 's2']);
		await stroke(page, -80, 100);
		await expect(layerRows(page)).toHaveCount(3);
	});

	test('undo goes back one stroke at a time, then removes the layer', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await addPaintLayer(page);
		await stroke(page, -80, -80);
		await stroke(page, -80, 60);
		const undo = page.locator('[data-frame-undo]');
		await expect(undo).toHaveAttribute('aria-label', 'Undo Brush stroke');
		await undo.click();
		await expect.poll(() => painted(page, -80, 60)).toBe(false);
		await expect.poll(() => painted(page, -80, -80)).toBe(true);
		await undo.click();
		await expect.poll(() => painted(page, -80, -80)).toBe(false);
		await expect(layerRows(page)).toHaveCount(1);
		await page.locator('[data-frame-redo]').click();
		await expect.poll(() => painted(page, -80, -80)).toBe(true);
		await expect.poll(() => painted(page, -80, 60)).toBe(false);
		await undo.click();
		await expect.poll(() => painted(page, -80, -80)).toBe(false);
		await undo.click();
		await expect(layerRows(page)).toHaveCount(0);
	});

	test('saving sends each layer as the file holding its strokes', async ({ page }) => {
		const log = await start(page);
		await openDusk(page);
		await addPaintLayer(page);
		await stroke(page, -80, -80);
		await addPaintLayer(page);
		await stroke(page, -80, 60);
		await page.locator('[data-frame-save]').click();
		await expect.poll(() => log.runs.length).toBe(1);
		const steps = log.runs[0].steps as Array<{ tool: string; params: { layer?: { item_id: string } } }>;
		expect(steps.map((step) => step.tool)).toEqual(['paint_layer', 'paint_layer']);
		const ids = steps.map((step) => step.params.layer!.item_id);
		expect(new Set(ids).size).toBe(2);
		for (const id of ids) {
			const opaque = await page.evaluate(async (url) => {
				const image = new Image();
				image.src = url;
				await image.decode();
				const canvas = document.createElement('canvas');
				canvas.width = image.naturalWidth;
				canvas.height = image.naturalHeight;
				const context = canvas.getContext('2d')!;
				context.drawImage(image, 0, 0);
				const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
				let count = 0;
				for (let i = 3; i < data.length; i += 4) if (data[i] > 0) count += 1;
				return { count, total: data.length / 4 };
			}, `/api/e2e-art/${id}.png`);
			expect(opaque.count).toBeGreaterThan(0);
			expect(opaque.count).toBeLessThan(opaque.total / 4);
		}
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
		await expect(railButton(page, 'paint.brush')).not.toHaveClass(/ai-ring/);
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
		for (const height of [900, 800, 700]) {
			await page.setViewportSize({ width: 1440, height });
			await expect.poll(overflow).toBeLessThanOrEqual(1);
			const now = await folded();
			if (now.includes('paint')) expect(now).toEqual(expect.arrayContaining(['fx', 'tools', 'select', 'look']));
			if (now.includes('select')) expect(now).toEqual(expect.arrayContaining(['fx', 'tools']));
			if (now.includes('generate')) expect(now).toEqual(expect.arrayContaining(['fx', 'tools', 'select', 'look', 'paint', 'shape']));
		}
		await page.setViewportSize({ width: 1440, height: 768 });
		await expect.poll(folded).toEqual(expect.arrayContaining(['fx', 'tools', 'select', 'look', 'paint', 'shape']));
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
		const sheet = page.getByRole('dialog', { name: 'Edits' });
		await expect(sheet.locator('[data-layers-add]')).toBeVisible();
		await expect(sheet.locator('[data-stack-live]')).toHaveCount(0);
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
		await expect(row(page, 's1')).toHaveAttribute('data-stack-kind', 'layer');
	}

	async function addLook(page: Page) {
		await selectRailTool(page, 'light');
		await contrast(page).fill('20');
		await contrast(page).dispatchEvent('change');
		await expect(row(page, 's2')).toBeVisible();
	}

	test('layers and edits sit in two panels: the paint step is a layer row with its opacity and eye, the look an edit', async ({ page }) => {
		await paintedLayer(page);
		await addLook(page);
		const keys = (section: string) => page.locator(`[data-edit-stack="panel"] [data-stack-section="${section}"] [data-stack-row]`).evaluateAll((rows) => rows.map((node) => node.getAttribute('data-stack-row')));
		expect(await keys('layers')).toEqual(['s1', 'original']);
		expect(await keys('edits')).toEqual(['s2']);
		await expect(row(page, 's1').locator('[data-stack-opacity]')).toHaveText('100%');
		await expect(row(page, 's1').locator('[data-stack-hide]')).toBeVisible();
		await expect(row(page, 's2')).toHaveAttribute('data-stack-kind', 'slim');
		await expect(row(page, 's2').locator('[data-stack-number]')).toHaveText('01');
		await expect(row(page, 's2')).not.toContainText('Applies to');
		await expect(page.locator('[data-stack-edits-hint]')).toHaveText('Edits apply to the whole layered picture.');
	});

	test('each panel folds from its header, keeps its count, remembers it and leaves the header buttons alone', async ({ page }) => {
		await paintedLayer(page);
		await addLook(page);
		const fold = (name: string) => page.locator(`[data-edit-stack="panel"] [data-stack-fold="${name}"]`);
		const section = (name: string) => page.locator(`[data-edit-stack="panel"] [data-stack-section="${name}"]`);
		await fold('edits').click();
		await expect(section('edits')).toHaveCount(0);
		await expect(section('layers')).toBeVisible();
		await expect(fold('edits')).toHaveAttribute('aria-expanded', 'false');
		await expect(page.locator('[data-stack-count="edits"]')).toHaveText('1');
		await page.waitForTimeout(300);
		await screenshot(page, JOURNEY, '42-folded-edits-1440');

		await fold('edits').focus();
		await page.keyboard.press('Enter');
		await expect(section('edits')).toBeVisible();
		await page.keyboard.press('Space');
		await expect(section('edits')).toHaveCount(0);

		await page.reload();
		await expect(page.locator('[data-image-editor]')).toBeVisible({ timeout: 30000 });
		await expect(fold('edits')).toHaveAttribute('aria-expanded', 'false');
		await expect(fold('layers')).toHaveAttribute('aria-expanded', 'true');
		await expect(section('layers')).toBeVisible({ timeout: 20000 });

		await page.locator('[data-layers-add]').click();
		await expect(page.locator('[data-layers-add-menu]')).toBeVisible();
		await expect(fold('layers')).toHaveAttribute('aria-expanded', 'true');
		await page.keyboard.press('Escape');
		await fold('edits').click();
		await expect(section('edits')).toBeVisible();
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

	test('a layer added after a look lands under it, so the look applies to the whole layered picture', async ({ page }) => {
		test.setTimeout(60000);
		await paintedLayer(page);
		await row(page, 's1').locator('[data-stack-open]').click();
		const slider = page.locator('[data-layer-drawer="s1"] input[type="range"]');
		await slider.fill('50');
		await slider.dispatchEvent('change');
		await addLook(page);
		await addPaintLayer(page);
		await stroke(page, 60, 60);
		const order = () => page.locator('[data-edit-stack="panel"] [data-stack-row]').evaluateAll((rows) => rows.map((node) => [node.getAttribute('data-stack-row'), Number(node.getAttribute('data-stack-index'))] as const).sort((a, b) => a[1] - b[1]).map(([key]) => key));
		await expect.poll(order).toEqual(['original', 's1', 's3', 's2']);
		await expect(page.locator('[data-edit-stack="panel"] [data-stack-section="edits"] [data-stack-row]')).toHaveCount(1);
		await selectRailTool(page, 'crop');
		await page.locator('[data-frame-undo]').click();
		await expect.poll(order).toEqual(['original', 's1', 's3', 's2']);
		await page.locator('[data-frame-undo]').click();
		await expect.poll(order).toEqual(['original', 's1', 's2']);
		await addPaintLayer(page);
		await stroke(page, -60, 60);
		await expect.poll(order).toEqual(['original', 's1', 's4', 's2']);
		await selectRailTool(page, 'crop');
		await page.locator('[data-options-panel] [data-crop-aspects] button', { hasText: '1:1' }).click();
		await page.locator('[data-options-apply]').click();
		await expect.poll(order).toEqual(['original', 's1', 's4', 's2', 's5']);
		await expect(page.locator('[data-edit-stack="panel"] [data-stack-section="edits"] [data-stack-number]')).toHaveText(['02', '01']);
		await expect(page.locator('[data-bracket]')).toHaveCount(0);
		await expect(row(page, 's1').locator('[data-stack-opacity]')).toHaveText('50%');
		await page.waitForTimeout(400);
		await screenshot(page, JOURNEY, '40-two-panels-1440');
	});
});

test.describe('Paint layers are kept one by one', () => {
	test.use({ viewport: { width: 1440, height: 1100 } });

	const row = (page: Page, id: string) => page.locator(`[data-edit-stack="panel"] [data-stack-row="${id}"]`);
	const fade = async (page: Page, id: string, value: string) => {
		await row(page, id).locator('[data-stack-open]').click();
		const slider = page.locator(`[data-layer-drawer="${id}"] input[type="range"]`);
		await slider.fill(value);
		await slider.dispatchEvent('change');
	};

	async function threeLayers(page: Page, log: Log) {
		await openDusk(page);
		await openPaint(page, 'brush');
		await stroke(page, -80, -70);
		await addPaintLayer(page);
		await stroke(page, -80, 0);
		await addPaintLayer(page);
		await stroke(page, -80, 70);
		await expect(layerRows(page)).toHaveCount(3);
		await expect.poll(() => log.artifacts).toBe(3);
	}

	test('three layers keep their name, order, fade and eye after leaving Paint and after a reload', async ({ page }) => {
		const log = await start(page, true);
		await threeLayers(page, log);
		await expect.poll(() => stackRows(page)).toEqual([ORIGINAL, expect.stringContaining('Paint1'), expect.stringContaining('Paint2'), expect.stringContaining('Paint3')]);
		await fade(page, 's2', '40');
		await row(page, 's3').locator('[data-stack-hide]').click();
		await expect(row(page, 's3')).toHaveAttribute('data-hidden', 'true');
		await screenshot(page, JOURNEY, '21-cmb408-built-1440');

		await page.reload();
		await expect(page.locator('[data-image-editor]')).toBeVisible({ timeout: 30000 });
		await expect(layerRows(page)).toHaveCount(3, { timeout: 20000 });
		await expect(row(page, 's2').locator('[data-stack-opacity]')).toHaveText('40%');
		await expect(row(page, 's3')).toHaveAttribute('data-hidden', 'true');
		await expect(row(page, 's1').locator('[data-stack-opacity]')).toHaveText('100%');
		await expect.poll(() => stackRows(page)).toEqual([ORIGINAL, expect.stringContaining('Paint1'), expect.stringContaining('Paint2'), expect.stringContaining('Paint3')]);

		await row(page, 's2').locator('[data-stack-open]').click();
		await expect(page.locator(CANVAS)).toBeVisible();
		await expect(page.locator('[data-paint-target="layer"]')).toContainText('Painting on Paint 2');
		await expect(page.locator('[data-layer-drawer="s2"]')).toBeVisible();
	});

	test('painting has no nested group: the Layers panel lists the layers and the selected one is the target', async ({ page }) => {
		const log = await start(page);
		await threeLayers(page, log);
		await row(page, 's2').locator('[data-stack-open]').click();
		await expect(page.locator(CANVAS)).toBeVisible();
		await expect(page.locator('[data-paint-target="layer"]')).toContainText('Painting on Paint 2');
		await expect(layerRows(page)).toHaveCount(3);
		await expect(page.locator('[data-paint-layers], [data-paint-group], [data-stack-group], [data-layer-row]')).toHaveCount(0);
		await expect(row(page, 's2')).toHaveAttribute('aria-current', 'true');
		await page.waitForTimeout(400);
		await screenshot(page, JOURNEY, '43-paint-no-nesting-1440');
	});

	test('one undo undoes the fade and the next undoes the stroke', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await openPaint(page, 'brush');
		await stroke(page);
		await row(page, 's1').locator('[data-stack-open]').click();
		await stroke(page, -60, 60);
		await fade(page, 's1', '50');
		const readout = row(page, 's1').locator('[data-stack-opacity]');
		await expect(readout).toHaveText('50%');
		await page.locator('[data-frame-undo]').click();
		await expect(readout).toHaveText('100%');
		await expect.poll(() => painted(page, -60, 60)).toBe(true);
		await page.locator('[data-frame-undo]').click();
		await expect.poll(() => painted(page, -60, 60)).toBe(false);
		await expect(readout).toHaveText('100%');
		await page.locator('[data-frame-redo]').click();
		await expect.poll(() => painted(page, -60, 60)).toBe(true);
		await expect(readout).toHaveText('100%');
		await page.locator('[data-frame-redo]').click();
		await expect(readout).toHaveText('50%');
	});

	test('reorder then Undo restores the order, Redo re-applies it', async ({ page }) => {
		const log = await start(page);
		await threeLayers(page, log);
		const before = (await layerOrder(page)) as string[];
		expect(before).toHaveLength(3);
		await row(page, before[0]).locator('[data-stack-open]').focus();
		await page.keyboard.press('Alt+ArrowUp');
		let moved: string[] = [];
		await expect.poll(async () => (moved = (await layerOrder(page)) as string[])).not.toEqual(before);
		await expect(layerRows(page)).toHaveCount(3);
		await expect(page.locator('[data-frame-undo]')).toHaveAttribute('aria-label', /Undo Move/);
		await page.locator('[data-frame-undo]').click();
		await expect.poll(() => layerOrder(page)).toEqual(before);
		await expect(layerRows(page)).toHaveCount(3);
		await page.locator('[data-frame-redo]').click();
		await expect.poll(() => layerOrder(page)).toEqual(moved);
		await page.locator('[data-frame-undo]').click();
		await expect.poll(() => layerOrder(page)).toEqual(before);
		await page.locator('[data-frame-undo]').click();
		await expect(layerRows(page)).toHaveCount(3);
		await expect.poll(() => painted(page, -80, 70)).toBe(false);
	});

	test('delete then Undo restores the layer with its pixels, Redo deletes it again', async ({ page }) => {
		const log = await start(page);
		await threeLayers(page, log);
		const before = (await layerOrder(page)) as string[];
		await row(page, 's2').hover();
		await row(page, 's2').locator('[data-stack-remove]').click();
		await expect(layerRows(page)).toHaveCount(2);
		await expect(page.locator('[data-frame-undo]')).toHaveAttribute('aria-label', /Undo Remove/);
		await page.locator('[data-frame-undo]').click();
		await expect(layerRows(page)).toHaveCount(3);
		await expect.poll(() => layerOrder(page)).toEqual(before);
		await row(page, 's2').locator('[data-stack-open]').click();
		await expect(page.locator(CANVAS)).toBeVisible();
		await expect.poll(() => painted(page, -80, 0)).toBe(true);
		await page.locator('[data-frame-redo]').click();
		await expect(layerRows(page)).toHaveCount(2);
		await page.locator('[data-frame-undo]').click();
		await expect(layerRows(page)).toHaveCount(3);
		await page.locator('[data-frame-undo]').click();
		await expect(layerRows(page)).toHaveCount(3);
		await expect.poll(() => painted(page, -80, 70)).toBe(false);
	});

	test('a layer duplicates and merges down from its menu', async ({ page }) => {
		const log = await start(page);
		await threeLayers(page, log);
		await row(page, 's3').locator('[data-stack-open]').click();
		await page.getByRole('button', { name: /^Layer options for/ }).click();
		await page.getByRole('menuitem', { name: 'Duplicate', exact: true }).click();
		await expect(layerRows(page)).toHaveCount(4);
		await expect(row(page, 's4')).toContainText('Paint 3 copy');
		await row(page, 's4').locator('[data-stack-open]').click();
		await page.getByRole('button', { name: /^Layer options for/ }).click();
		await page.getByRole('menuitem', { name: 'Merge down', exact: true }).click();
		await expect(layerRows(page)).toHaveCount(3);
		await expect.poll(() => log.artifacts).toBe(4);
	});
});

test.describe('Adding layers from the Edits list', () => {
	test.use({ viewport: { width: 1440, height: 1100 } });

	const row = (page: Page, id: string) => panel(page).locator(`[data-stack-row="${id}"]`);
	const add = async (page: Page, item: string) => {
		await panel(page).locator('[data-layers-add]').click();
		await page.locator(`[data-layers-add-item="${item}"]`).click();
		if (item === 'new') await ready(page);
	};

	test('an empty layer goes above the selected one, a picture joins it, and the saved file keeps the order', async ({ page }) => {
		const log = await start(page);
		await openDusk(page);
		await openPaint(page, 'brush');
		await stroke(page, -80, -70);
		await add(page, 'new');
		await stroke(page, -80, 40);
		await expect.poll(() => layerOrder(page)).toEqual(['s1', 's2']);

		await row(page, 's1').locator('[data-stack-open]').click();
		await expect(page.locator(CANVAS)).toBeVisible();
		await expect.poll(() => painted(page, -80, 40)).toBe(true);
		await panel(page).locator('[data-layers-add]').click();
		await expect(page.locator('[data-layers-add-heading]')).toHaveText('Add above Paint 1');
		await screenshot(page, JOURNEY, '22-fe391-add-menu-1440');
		await page.locator('[data-layers-add-item="new"]').click();
		await ready(page);
		await expect.poll(() => layerOrder(page)).toEqual(['s1', 's3', 's2']);
		await stroke(page, 60, -60);
		await expect.poll(() => layerOrder(page)).toEqual(['s1', 's3', 's2']);

		const chooser = page.waitForEvent('filechooser');
		await add(page, 'file');
		await (await chooser).setFiles({ name: 'badge.png', mimeType: 'image/png', buffer: colourPng(160, 120, [240, 190, 40]) });
		await expect(page.locator(CANVAS)).toBeVisible();
		await expect.poll(() => layerOrder(page)).toEqual(['s1', 's3', 's4', 's2']);
		await expect(row(page, 's4')).toContainText('badge');

		await row(page, 's2').locator('[data-stack-open]').focus();
		await page.keyboard.press('Alt+ArrowDown');
		await expect.poll(() => layerOrder(page)).toEqual(['s1', 's3', 's2', 's4']);

		await row(page, 's3').locator('[data-stack-hide]').click();
		await expect(row(page, 's3')).toHaveAttribute('data-hidden', 'true');

		await page.locator('[data-frame-save]').click();
		await expect.poll(() => log.runs.length).toBe(1);
		const steps = log.runs[0].steps as Array<{ tool: string; hidden?: boolean }>;
		expect(steps.map((step) => step.tool)).toEqual(['paint_layer', 'paint_layer', 'paint_layer', 'paint_layer']);
		expect(steps.map((step) => step.hidden === true)).toEqual([false, true, false, false]);
	});

	test('a layer renames from its menu and Duplicate works on a picture layer', async ({ page }) => {
		await start(page);
		await openDusk(page);
		await openPaint(page, 'brush');
		const chooser = page.waitForEvent('filechooser');
		await add(page, 'file');
		await (await chooser).setFiles({ name: 'badge.png', mimeType: 'image/png', buffer: colourPng(160, 120, [240, 190, 40]) });
		await expect(page.locator(CANVAS)).toBeVisible();
		await expect.poll(() => layerOrder(page)).toEqual(['s1']);
		await page.waitForTimeout(400);
		await screenshot(page, JOURNEY, '23-fe391-picture-layer-1440');
		await row(page, 's1').locator('[data-stack-open]').click();
		await page.getByRole('button', { name: /^Layer options for/ }).click();
		await page.getByRole('menuitem', { name: 'Rename', exact: true }).click();
		const input = row(page, 's1').locator('[data-stack-rename]');
		await expect(input).toBeVisible();
		await input.fill('Badge');
		await input.press('Enter');
		await expect(row(page, 's1')).toContainText('Badge');
		await page.getByRole('button', { name: /^Layer options for/ }).click();
		await page.getByRole('menuitem', { name: 'Duplicate', exact: true }).click();
		await expect.poll(() => layerOrder(page)).toEqual(['s1', 's2']);
		await expect(row(page, 's2')).toContainText('Badge copy');
	});
});

test.describe('Adding layers on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('the Edits sheet has a 44 high add button and 48 high rows, and a new layer opens in Paint', async ({ page }) => {
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
		await page.locator('[data-rail-sheet="paint"] [data-sheet-item="paint.brush"]').click();
		await expect(phone.locator('[data-paint-tool-panel="brush"]')).toBeVisible();
		await phone.locator('[data-frame-edits]').click();
		const sheet = page.getByRole('dialog', { name: 'Edits' });
		const plus = sheet.locator('[data-layers-add]');
		const size = (await plus.boundingBox())!;
		expect(size.width).toBeGreaterThanOrEqual(44);
		expect(size.height).toBeGreaterThanOrEqual(44);
		await plus.click();
		const item = page.locator('[data-layers-add-item="new"]');
		expect((await item.boundingBox())!.height).toBeGreaterThanOrEqual(48);
		await screenshot(page, JOURNEY, '24-fe391-390');
		await item.click();
		await expect(sheet).toHaveCount(0);
		await expect(phone.locator(CANVAS)).toBeVisible();
	});
});
