import { test, expect, type Page, type Route } from '@playwright/test';
import { clearEdits, editorMenu, loginAsOwner, openEditorMenu, screenshot, selectRailTool, railButton } from './helpers';
import { solidPng } from './mediaToolMocks';

const JOURNEY = 'image-editor-tools-map';
const GENERATION_ID = 'gen-e2e-tools-map';
const FILE_ID = 504;
const SOURCE_FILE = `outputs/2026-11-01/${GENERATION_ID}/harbor-dawn.png`;
const EDIT_ID = 'ed_e2e_map';

type Json = Record<string, unknown>;

const OLD_STEPS = [
	{ id: 's1', tool: 'effects', icon: 'editor-effects', label: 'Tone', summary: 'Brightness 20 · Contrast 10 · Saturation -15', hidden: false, params: { kind: 'adjust', op: 'tone', name: 'Tone', values: { brightness: 20, contrast: 10, saturation: -15, hue: 0 } } },
	{ id: 's2', tool: 'effects', icon: 'editor-effects', label: 'Exposure', summary: 'Stops 0.5', hidden: false, params: { kind: 'adjust', op: 'exposure', name: 'Exposure', values: { stops: 0.5 } } },
	{ id: 's3', tool: 'effects', icon: 'editor-effects', label: 'Vignette', summary: 'Amount 30', hidden: false, params: { kind: 'adjust', op: 'vignette', name: 'Vignette', values: { amount: 30, midpoint: 50, feather: 60 } } },
	{ id: 's4', tool: 'effects', icon: 'editor-effects', label: 'Grain', summary: 'Amount 20', hidden: false, params: { kind: 'adjust', op: 'grain', name: 'Grain', values: { amount: 20, size: 1, seed: 7 } } }
];

const OLD_STATE = {
	v: 1,
	stack: { v: 1, steps: OLD_STEPS, cursor: 4, pending: null, seq: 4 },
	activeTool: 'adjust',
	viewport: { fit: true, zoom: 1, panX: 0, panY: 0 },
	mask: null,
	saved: null
};

interface Server {
	creates: Json[];
	saves: Json[];
	revision: number;
	remoteState: Json | null;
	conflictNext: boolean;
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
		files: [{ id: FILE_ID, file_path: SOURCE_FILE, file_type: 'image', is_final: true, created_at: now, width: 640, height: 480 }]
	};
}

async function ok(route: Route, data: unknown) {
	await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

function record(server: Server, id: string, state: Json | null): Json {
	const now = new Date().toISOString();
	return { id, tool: 'image', name: null, source: { kind: 'history', id: String(FILE_ID) }, thumbnail_upload_id: null, revision: server.revision, created_at: now, updated_at: now, ...(state ? { state } : {}) };
}

async function mockServer(page: Page): Promise<Server> {
	const server: Server = { creates: [], saves: [], revision: 1, remoteState: null, conflictNext: false };
	const source = solidPng(640, 480);
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) => (route.request().method() === 'GET' ? ok(route, { generations: [generation()], total: 1 }) : route.fallback()));
	await page.route(`**/api/media/generations/${GENERATION_ID}/harbor-dawn.png*`, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: source }));
	await page.route(/\/api\/edits(\/[^/?]+)?(\?.*)?$/, async (route) => {
		const request = route.request();
		const method = request.method();
		const id = new URL(request.url()).pathname.split('/')[3] ?? null;
		if (method === 'POST' && !id) {
			const body = request.postDataJSON() as Json;
			server.creates.push(body);
			server.revision = 1;
			return ok(route, { edit: record(server, EDIT_ID, body.state as Json) });
		}
		if (method === 'GET' && id) return ok(route, { edit: record(server, id, server.remoteState ?? ((server.creates[0]?.state as Json) ?? {})) });
		if (method === 'PUT' && id) {
			const body = request.postDataJSON() as Json;
			server.saves.push(body);
			if (server.conflictNext || body.revision !== server.revision) {
				server.conflictNext = false;
				server.revision += 1;
				return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: { success: false, error: 'edit_revision_conflict', message: 'changed', revision: server.revision } }) });
			}
			server.revision += 1;
			return ok(route, { edit: record(server, id, null) });
		}
		return route.fallback();
	});
	return server;
}

const rail = (page: Page, tool: string) => page.locator(`[data-tool-rail] button[data-tool="${tool}"]`);
const group = (page: Page, id: string) => page.locator(`[data-tool-rail] button[data-group-button="${id}"]`);
const folded = (page: Page) => page.locator('[data-tool-rail]').first().getAttribute('data-rail-folded');
const FIELD_OF: Record<string, string> = { Contrast: 'tone_contrast', Saturation: 'tone_saturation' };
const slider = (page: Page, label: string) => page.locator(`[data-field-name="${FIELD_OF[label] ?? label}"] input[type="range"]`);

async function begin(page: Page): Promise<Server> {
	const server = await mockServer(page);
	await loginAsOwner(page);
	await clearEdits(page);
	await page.goto('/generate');
	const phone = (page.viewportSize()?.width ?? 1440) < 768;
	await expect(page.locator(phone ? '.studio-topbar' : '.tab-bar-container')).toBeVisible({ timeout: 15000 });
	return server;
}

async function openHarbor(page: Page) {
	if ((page.viewportSize()?.width ?? 1440) < 768) {
		await page.getByRole('button', { name: /^Open tabs/ }).click();
		await page.getByRole('dialog', { name: 'Tabs' }).getByRole('button', { name: 'Image editor' }).click();
		await page.getByTestId('phone-tool-tab').locator('[data-start-source="history"]').click();
		await page.getByTestId('history-file-tile').first().locator('[data-pick-main]').click();
		await expect(page.locator('[data-editor-frame="phone"]')).toBeVisible();
		return;
	}
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
	const empty = page.getByTestId('tool-tab-empty');
	await expect(empty).toContainText('Open a picture to edit');
	await empty.locator('[data-start-source="history"]').click();
	await page.getByTestId('history-file-tile').first().locator('[data-pick-main]').click();
	await expect(page.locator('[data-image-editor]')).toBeVisible();
}

async function setRailHeight(page: Page, wanted: number) {
	await page.setViewportSize({ width: 1440, height: 1200 });
	const current = await page.locator('[data-tool-rail]').first().evaluate((node) => (node as HTMLElement).clientHeight);
	const chrome = 1200 - current;
	await page.setViewportSize({ width: 1440, height: chrome + wanted });
	await page.waitForTimeout(150);
}

test.describe('the Image editor tools map on desktop', () => {
	test.use({ viewport: { width: 1440, height: 1100 } });

	test('a tall window shows every entry as its own icon, in group order, with a divider between groups and no flyouts', async ({ page }) => {
		await begin(page);
		await openHarbor(page);
		await setRailHeight(page, 1169);
		const ids = await page.locator('[data-tool-rail] button[data-tool]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-tool')));
		expect(ids.slice(0, 6)).toEqual(['edit_region', 'remove_object', 'crop', 'resize', 'canvas', 'slice']);
		expect(ids).toContain('film_grain');
		expect(ids).toContain('lut');
		expect(ids).toContain('effects');
		expect(ids.indexOf('light')).toBeLessThan(ids.indexOf('film_grain'));
		expect(ids.indexOf('film_grain')).toBeLessThan(ids.indexOf('effects'));
		expect(ids.indexOf('effects')).toBeLessThan(ids.indexOf('colorKey'));
		await expect(page.locator('[data-tool-rail] [data-group-button]')).toHaveCount(0);
		await expect(page.locator('[data-tool-rail] [role="separator"]')).toHaveCount(7);
		expect(await page.locator('[data-tool-rail]').first().evaluate((node) => (node as HTMLElement).scrollHeight <= (node as HTMLElement).clientHeight)).toBe(true);
		await screenshot(page, JOURNEY, '01-rail-tall-dark-1440');
	});

	test('shorter windows fold Effects first, then Cut out, Select and move, Light and colour, Paint and Transform, with no scrollbar', async ({ page }) => {
		await begin(page);
		await openHarbor(page);
		await setRailHeight(page, 1043);
		expect(await folded(page)).toBe('fx');
		await expect(group(page, 'fx')).toBeVisible();
		await expect(rail(page, 'film_grain')).toHaveCount(0);
		await expect(rail(page, 'light')).toBeVisible();
		await screenshot(page, JOURNEY, '01c-rail-900-dark-1440');
		await setRailHeight(page, 900 - 118);
		expect(await folded(page)).toBe('fx tools select look');
		await expect(group(page, 'look')).toBeVisible();
		await expect(group(page, 'tools')).toBeVisible();
		await expect(group(page, 'select')).toBeVisible();
		await expect(rail(page, 'crop')).toBeVisible();
		await expect(rail(page, 'paint.brush')).toBeVisible();
		await expect(rail(page, 'edit_region')).toBeVisible();
		await setRailHeight(page, 720 - 118);
		expect(await folded(page)).toBe('fx tools select look paint shape');
		await expect(group(page, 'paint')).toBeVisible();
		await expect(group(page, 'shape')).toBeVisible();
		await expect(rail(page, 'edit_region')).toBeVisible();
		expect(await page.locator('[data-tool-rail]').first().evaluate((node) => (node as HTMLElement).scrollHeight <= (node as HTMLElement).clientHeight)).toBe(true);
		await screenshot(page, JOURNEY, '01d-rail-720-dark-1440');
	});

	test('the fold at 720, 800, 900, 1000 and 1100 high editors', async ({ page }) => {
		await begin(page);
		await openHarbor(page);
		const expected: Record<number, { folded: string; icons: number }> = {
			720: { folded: 'fx tools select look paint shape', icons: 9 },
			800: { folded: 'fx tools select look paint', icons: 12 },
			900: { folded: 'fx tools select look', icons: 15 },
			1000: { folded: 'fx tools select look', icons: 15 },
			1100: { folded: 'fx tools select', icons: 19 }
		};
		for (const height of [720, 800, 900, 1000, 1100]) {
			await setRailHeight(page, height - 118);
			const state = { folded: (await folded(page)) ?? '', icons: await page.locator('[data-tool-rail] > button').count() };
			test.info().annotations.push({ type: `fold-${height}`, description: `${state.folded} | ${state.icons} icons` });
			expect(state, `${height} high`).toEqual(expected[height]);
		}
	});

	test('picking from a flyout selects the entry, closes it, and selecting never moves an icon', async ({ page }) => {
		await begin(page);
		await openHarbor(page);
		await setRailHeight(page, 900 - 118);
		const positions = () => page.locator('[data-tool-rail] > button').evaluateAll((nodes) => nodes.map((node) => Math.round(node.getBoundingClientRect().top)));
		const before = await positions();
		await group(page, 'fx').click();
		const flyout = page.locator('[data-rail-flyout="fx"]');
		await expect(flyout).toBeVisible();
		await expect(flyout.locator('[data-flyout-item]')).toHaveCount(4);
		const box = (await flyout.boundingBox())!;
		const trigger = (await group(page, 'fx').boundingBox())!;
		expect(box.x).toBeGreaterThanOrEqual(trigger.x + trigger.width);
		await screenshot(page, JOURNEY, '02-effects-flyout-dark-1440');
		await flyout.locator('[data-flyout-item="vignette"]').click();
		await expect(flyout).toHaveCount(0);
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'vignette');
		await expect(group(page, 'fx')).toHaveAttribute('data-shown', 'vignette');
		expect(await positions()).toEqual(before);
		await selectRailTool(page, 'effects');
		await expect(group(page, 'fx')).toHaveAttribute('data-shown', 'vignette');
		expect(await positions()).toEqual(before);
	});

	test('Escape closes the flyout before anything else and returns focus to the icon', async ({ page }) => {
		await begin(page);
		await openHarbor(page);
		await setRailHeight(page, 900 - 118);
		await group(page, 'fx').focus();
		await page.keyboard.press('Enter');
		await expect(page.locator('[data-rail-flyout]')).toBeVisible();
		await page.keyboard.press('ArrowDown');
		await page.keyboard.press('Escape');
		await expect(page.locator('[data-rail-flyout]')).toHaveCount(0);
		await expect(group(page, 'fx')).toBeFocused();
	});

	test('Film grain and Vignette are one entry each with their own panel and live edits', async ({ page }) => {
		await begin(page);
		await openHarbor(page);
		await setRailHeight(page, 1169);
		await selectRailTool(page, 'film_grain');
		const panel = page.locator('[data-entry-options="film_grain"]');
		await expect(panel).toBeVisible();
		await expect(page.locator('[data-options-apply]')).toHaveCount(0);
		await expect(panel.locator('[data-field-type]:not([data-field-type="row"])')).toHaveCount(4);
		await screenshot(page, JOURNEY, '03-film-grain-dark-1440');
		const amount = panel.locator('[data-field-name="amount"] input').first();
		await amount.fill('42');
		await amount.press('Tab');
		await expect(page.locator('[data-edit-stack] [data-stack-section="edits"] [data-stack-row]').first()).toContainText('Film grain');
		await expect(railButton(page, 'film_grain').locator('[data-tool-applied]')).toBeVisible();
		await selectRailTool(page, 'vignette');
		await expect(page.locator('[data-entry-options="vignette"] [data-field-type]:not([data-field-type="row"])')).toHaveCount(3);
		await screenshot(page, JOURNEY, '04-vignette-dark-1440');
	});

	test('Light then Colour write two steps and neither changes the other', async ({ page }) => {
		await begin(page);
		await openHarbor(page);
		await setRailHeight(page, 1169);
		await selectRailTool(page, 'light');
		await expect(page.locator('[data-entry-options="light"] [data-field-type="slider"]')).toHaveCount(5);
		const contrast = slider(page, 'Contrast');
		await contrast.fill('12');
		await contrast.dispatchEvent('change');
		await screenshot(page, JOURNEY, '06-light-dark-1440');
		await selectRailTool(page, 'colour');
		await expect(page.locator('[data-entry-options="colour"] [data-field-type="slider"]')).toHaveCount(5);
		await slider(page, 'Saturation').fill('-20');
		await slider(page, 'Saturation').dispatchEvent('change');
		const rows = page.locator('[data-edit-stack] [data-stack-section="edits"] [data-stack-row]');
		await expect(rows).toHaveCount(2);
		await expect(rows.nth(1)).toContainText('Light');
		await expect(rows.nth(0)).toContainText('Colour');
		await selectRailTool(page, 'light');
		await expect(slider(page, 'Contrast')).toHaveValue('12');
		await expect(page.locator('[data-entry-options="light"] [data-field-name="tone_saturation"]')).toHaveCount(0);
		await rows.nth(0).locator('button').first().click();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'colour');
		await expect(slider(page, 'Saturation')).toHaveValue('-20');
	});

	test('Light and colour in a folded rail opens a flyout of five entries', async ({ page }) => {
		await begin(page);
		await openHarbor(page);
		await setRailHeight(page, 720 - 118);
		await group(page, 'look').click();
		await expect(page.locator('[data-rail-flyout="look"] [data-flyout-item]')).toHaveCount(5);
		await screenshot(page, JOURNEY, '05-light-colour-flyout-dark-1440');
	});

	test('Looks is its own entry, its panel lists the looks and nothing sits under the canvas', async ({ page }) => {
		await begin(page);
		await openHarbor(page);
		await setRailHeight(page, 1169);
		await selectRailTool(page, 'effects');
		await expect(page.locator('[data-options-panel] [data-look-list]')).toBeVisible();
		await expect(page.locator('[data-tray]')).toHaveCount(0);
		await page.locator('[data-look-item]:not([data-look-item="__original"])').first().click();
		await expect(page.locator('[data-options-panel] [data-field-name="intensity"]')).toBeVisible();
		await screenshot(page, JOURNEY, '07-looks-dark-1440');
	});

	test('the marking block is the same in Edit a region and Remove an object, Clear area is undoable', async ({ page }) => {
		await begin(page);
		await openHarbor(page);
		await setRailHeight(page, 1169);
		await expect(rail(page, 'mask')).toHaveCount(0);
		await selectRailTool(page, 'edit_region');
		const block = page.locator('[data-options-panel] [data-mask-area-readout]');
		await expect(block).toBeVisible();
		const markup = async () => page.locator('[data-options-panel]').evaluate((node) => Array.from(node.querySelectorAll('[aria-label="Marking tool"] button, [aria-label="Mask brush"] button, [aria-label="Lasso mode"] button')).map((button) => (button.textContent ?? '').trim()).join('|'));
		const first = await markup();
		expect(first).toContain('Brush');
		expect(first).toContain('Lasso');
		await screenshot(page, JOURNEY, '08-edit-region-dark-1440');
		await selectRailTool(page, 'remove_object');
		await expect(page.locator('[data-options-panel] [data-mask-area-readout]')).toBeVisible();
		expect(await markup()).toBe(first);
		await screenshot(page, JOURNEY, '13-remove-object-dark-1440');
		const layer = page.locator('[data-mask-layer]');
		await expect(layer).toBeVisible();
		const box = (await layer.boundingBox())!;
		await page.getByRole('group', { name: 'Marking tool' }).getByRole('button', { name: 'Lasso', exact: true }).click();
		await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.3);
		await page.mouse.down();
		for (const [x, y] of [[0.6, 0.3], [0.6, 0.6], [0.3, 0.6], [0.3, 0.3]]) await page.mouse.move(box.x + box.width * x, box.y + box.height * y, { steps: 4 });
		await page.mouse.up();
		await expect(page.locator('[data-mask-area]')).toBeVisible();
		await page.locator('[data-options-panel]').getByRole('button', { name: 'Clear area' }).click();
		await expect(page.locator('[data-mask-area]')).toBeHidden();
		await page.locator('[data-frame-undo]').click();
		await expect(page.locator('[data-mask-area]')).toBeVisible();
	});

	test('the Tools menu mirrors the groups however the rail folded', async ({ page }) => {
		await begin(page);
		await openHarbor(page);
		await setRailHeight(page, 720 - 118);
		await openEditorMenu(page, 'Tools');
		const menu = page.locator('[data-editor-menu="tools"]');
		await expect(menu.locator('[data-menu-row]')).toHaveCount(8);
		await menu.locator('[data-row-id="toolgroup:fx"]').hover();
		await expect(page.locator('[data-editor-submenu="toolgroup:fx"] [data-menu-row]')).toHaveCount(4);
		await screenshot(page, JOURNEY, '10-tools-menu-dark-1440');
		await page.keyboard.press('Escape');
		await page.keyboard.press('Escape');
		await editorMenu(page, 'Tools', 'Effects', 'Invert');
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'invert');
	});

	test('an edit saved before the change opens in the entries that own its steps and edits in place', async ({ page }) => {
		const server = await begin(page);
		await openHarbor(page);
		await setRailHeight(page, 1169);
		await selectRailTool(page, 'light');
		await slider(page, 'Contrast').fill('5');
		await slider(page, 'Contrast').dispatchEvent('change');
		await expect.poll(() => server.creates.length).toBe(1);
		server.remoteState = OLD_STATE;
		server.conflictNext = true;
		await slider(page, 'Contrast').fill('6');
		await slider(page, 'Contrast').dispatchEvent('change');
		await page.getByRole('button', { name: 'Load the newer one' }).click();
		const rows = page.locator('[data-edit-stack] [data-stack-section="edits"] [data-stack-row]');
		await expect(rows).toHaveCount(4);
		await expect(rows.nth(3)).toContainText('Tone');
		await expect(rows.nth(0)).toContainText('Grain');
		await rows.nth(3).locator('button').first().click();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'light');
		await expect(slider(page, 'Contrast')).toHaveValue('10');
		await rows.nth(0).locator('button').first().click();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'film_grain');
		await selectRailTool(page, 'light');
		const before = server.saves.length;
		await slider(page, 'Contrast').fill('33');
		await slider(page, 'Contrast').dispatchEvent('change');
		await expect.poll(() => server.saves.length).toBeGreaterThan(before);
		const last = server.saves[server.saves.length - 1] as { state: { stack: { steps: Array<Json> } } };
		const steps = last.state.stack.steps;
		expect(steps).toHaveLength(4);
		expect((steps[0].params as Json).values).toEqual({ brightness: 20, contrast: 33, saturation: -15, hue: 0 });
		expect(steps.slice(1)).toEqual(OLD_STEPS.slice(1));
	});
	test('the option panels are fields: Curves, Crop with its box, Resize and Slice', async ({ page }) => {
		await begin(page);
		await openHarbor(page);
		await setRailHeight(page, 1169);
		await selectRailTool(page, 'curves');
		await expect(page.locator('[data-entry-options="curves"] [data-field-type="slider"]')).toHaveCount(3);
		await screenshot(page, JOURNEY, '40-form-curves-dark-1440');
		await selectRailTool(page, 'crop');
		await expect(page.locator('[data-crop-fields] [data-field-name]')).toHaveCount(6);
		await page.locator('[data-options-panel] [data-crop-aspects] button', { hasText: '1:1' }).click();
		await expect(page.locator('[data-crop-corner]').first()).toBeVisible();
		const width = page.locator('[aria-label="Crop width"]');
		const before = await width.inputValue();
		await width.fill('200');
		await width.press('Enter');
		await expect(page.locator('[aria-label="Crop width"]')).toHaveValue('200');
		expect(before).not.toBe('200');
		await screenshot(page, JOURNEY, '40-form-crop-dark-1440');
		await selectRailTool(page, 'resize');
		await expect(page.locator('[data-field-name="width"] input')).toBeVisible();
		await screenshot(page, JOURNEY, '40-form-resize-dark-1440');
		await selectRailTool(page, 'slice');
		await page.locator('[data-slice-preset="3x2"]').first().click();
		await expect(page.locator('[data-field-name="cols"] input')).toHaveValue('3');
		await screenshot(page, JOURNEY, '40-form-slice-dark-1440');
		await page.locator('[data-slice-tab="output"]').click();
		await screenshot(page, JOURNEY, '40-form-slice-output-dark-1440');
	});

	test('Light shows its sliders as fields', async ({ page }) => {
		await begin(page);
		await openHarbor(page);
		await setRailHeight(page, 1169);
		await selectRailTool(page, 'light');
		await slider(page, 'Contrast').fill('24');
		await slider(page, 'Contrast').dispatchEvent('change');
		await screenshot(page, JOURNEY, '40-form-light-dark-1440');
		await selectRailTool(page, 'edit_region');
		await screenshot(page, JOURNEY, '40-form-edit-region-dark-1440');
	});

});

test.describe('the Image editor tools map on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('the strip shows groups and a multi-entry group opens a sheet of its entries', async ({ page }) => {
		await begin(page);
		await openHarbor(page);
		const phone = page.getByTestId('phone-tool-tab');
		const strip = phone.locator('[data-tool-rail="strip"]');
		await expect(strip.locator('[data-group-button]')).toHaveCount(7);
		await strip.locator('[data-group-button="fx"]').click();
		const sheet = page.locator('[data-rail-sheet="fx"]');
		await expect(sheet.locator('[data-sheet-item]')).toHaveCount(4);
		await screenshot(page, JOURNEY, '11-phone-group-sheet-dark-390');
		await sheet.locator('[data-sheet-item="film_grain"]').click();
		await expect(sheet).toHaveCount(0);
		await expect(phone.locator('[data-entry-options="film_grain"]')).toBeVisible();
		await screenshot(page, JOURNEY, '12-phone-film-grain-dark-390');
	});

	test('the option panels are fields on a phone', async ({ page }) => {
		await begin(page);
		await openHarbor(page);
		const phone = page.getByTestId('phone-tool-tab');
		for (const id of ['light', 'curves', 'resize', 'slice', 'edit_region']) {
			await selectRailTool(page, id, phone);
			await expect(phone.locator(id === 'edit_region' ? '[data-mask-compact]' : '[data-options-panel]').first()).toBeVisible();
			await screenshot(page, JOURNEY, `40-form-${id}-dark-390`);
		}
		await selectRailTool(page, 'crop', phone);
		await screenshot(page, JOURNEY, '40-form-crop-dark-390');
	});
});

test.describe('Edits rows on desktop', () => {
	test.use({ viewport: { width: 1440, height: 1100 } });

	test('a clicked step is only selected, a changed value marks it Editing with its icons, a new edit is Not applied', async ({ page }) => {
		await begin(page);
		await openHarbor(page);
		await setRailHeight(page, 1169);
		const rows = page.locator('[data-edit-stack] [data-stack-section="edits"] [data-stack-row]');
		const width = page.locator('[data-field-name="width"] input');
		await selectRailTool(page, 'resize');
		await width.fill('320');
		await width.press('Enter');
		await expect(page.locator('[data-stack-row="pending"]')).toContainText('Not applied');
		await expect(page.locator('[data-stack-row="pending"]')).not.toContainText('PENDING');
		await expect(page.locator('[data-stack-row="pending"] [data-stack-actions]')).toHaveCount(0);
		await screenshot(page, JOURNEY, '60-edits-rows-new-dark-1440');
		await page.locator('[data-options-apply]').first().click();
		await expect(rows).toHaveCount(1);
		await selectRailTool(page, 'crop');
		await rows.nth(0).locator('button').first().click();
		await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'resize');
		await expect(rows.nth(0)).toHaveAttribute('data-status', 'selected');
		await expect(rows.nth(0)).not.toContainText('Editing');
		await expect(rows.nth(0).locator('[data-stack-hide]')).toBeVisible();
		await screenshot(page, JOURNEY, '60-edits-rows-selected-dark-1440');
		await width.fill('400');
		await width.press('Enter');
		await expect(rows.nth(0)).toHaveAttribute('data-status', 'editing');
		await expect(rows.nth(0)).toContainText('Editing');
		await expect(rows.nth(0)).not.toContainText('PENDING');
		await expect(rows.nth(0).locator('[data-stack-hide]')).toBeVisible();
		await expect(rows.nth(0).locator('[data-stack-remove]')).toBeVisible();
		await screenshot(page, JOURNEY, '60-edits-rows-editing-dark-1440');
	});
});
