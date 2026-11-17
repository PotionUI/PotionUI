import { test, expect, type Page, type Request } from '@playwright/test';
import { clearEdits, editorMenu, editorMenuItem, loginAsOwner, openEditorMenu, ownerToken, screenshot } from './helpers';
import { solidPng } from './mediaToolMocks';

const JOURNEY = 'image-editor-plugin-slots';
const PLUGIN = 'e2e-editor-slots';
const GROUP = `${PLUGIN}.stylize`;
const TINT = `${PLUGIN}.tint`;
const TINT_LATER = `${PLUGIN}.tint-later`;
const FAULTY = `${PLUGIN}.faulty`;
const SPOTLIGHT = `${PLUGIN}.spotlight`;
const RELIGHT = `${PLUGIN}.relight`;

type Json = Record<string, any>;

test.use({ viewport: { width: 1440, height: 900 } });

async function headers(page: Page) {
	return { Authorization: `Bearer ${await ownerToken(page)}` };
}

async function setPlugin(page: Page, enabled: boolean) {
	const auth = await headers(page);
	await page.request.post('/api/plugins/scan', { headers: auth });
	const response = await page.request.post(`/api/plugins/${PLUGIN}/${enabled ? 'enable' : 'disable'}`, { headers: auth });
	if (enabled) expect(response.ok(), `enable ${PLUGIN} -> ${response.status()}`).toBeTruthy();
}

async function begin(page: Page, pluginOn: boolean) {
	await loginAsOwner(page);
	await clearEdits(page);
	await setPlugin(page, pluginOn);
}

async function openPicture(page: Page) {
	const auth = await headers(page);
	const upload = await page.request.post('/api/media/upload', {
		headers: auth,
		multipart: { file: { name: `slots-${Date.now()}.png`, mimeType: 'image/png', buffer: solidPng(640, 480) } }
	});
	expect(upload.ok(), `upload -> ${upload.status()}`).toBeTruthy();
	await page.goto('/generate');
	await expect(page.locator('.tab-bar-container')).toBeVisible({ timeout: 15000 });
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
	const empty = page.getByTestId('tool-tab-empty');
	await expect(empty).toContainText('Open a picture to edit');
	await empty.getByRole('button', { name: 'Library', exact: true }).click();
	await page.locator('[data-pick-tile] [data-pick-main]').first().click();
	await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop', { timeout: 30000 });
}

function rail(page: Page, tool: string) {
	return page.locator(`[data-tool-rail] button[data-tool="${tool}"]`);
}

function stackRow(page: Page, label: string) {
	return page.locator('[data-edit-stack="panel"] [data-stack-row]:not([data-stack-row="pending"])', { hasText: label });
}

async function openTint(page: Page, tool = TINT, panel = 'Tint') {
	await rail(page, tool).click();
	await expect(page.locator(`[data-e2e-slot-options="${panel}"]`)).toBeVisible();
	await expect(page.locator('[data-stack-row="pending"]')).toContainText('Strength 50');
}

async function applyTint(page: Page) {
	await openTint(page, TINT_LATER, 'Tint later');
	await page.locator('[data-options-apply]').click();
	await expect(page.locator('[data-stack-row="pending"]')).toHaveCount(0);
	await expect(stackRow(page, 'Tint')).toContainText('Strength 50');
}

async function applyCrop(page: Page) {
	await rail(page, 'crop').click();
	const apply = page.locator('[data-options-apply]');
	for (const aspect of ['16:9', '1:1', '9:16']) {
		await page.locator('[data-options-panel] [data-crop-aspects] button', { hasText: aspect }).click();
		if (await apply.isEnabled()) break;
	}
	await apply.click();
	await expect(stackRow(page, 'Crop')).toBeVisible();
}

async function savedSteps(page: Page): Promise<Json[]> {
	const auth = await headers(page);
	const list = await page.request.get('/api/edits?limit=20', { headers: auth });
	const edits = ((await list.json()).data?.edits ?? []) as Array<{ id: string }>;
	if (!edits.length) return [];
	const detail = await page.request.get(`/api/edits/${encodeURIComponent(edits[0].id)}`, { headers: auth });
	return ((await detail.json()).data?.edit?.state?.stack?.steps ?? []) as Json[];
}

function watchErrors(page: Page): string[] {
	const errors: string[] = [];
	page.on('pageerror', (error) => errors.push(`${error.name}: ${error.message}`));
	return errors;
}

test.describe('a plugin fills the image editor slots', () => {
	test('its group, tool and marking tool show up in the rail and the Mask tool', async ({ page }) => {
		await begin(page, true);
		await openPicture(page);

		await expect(rail(page, TINT)).toBeVisible();
		await expect(rail(page, TINT_LATER)).toBeVisible();
		await expect(rail(page, FAULTY)).toBeVisible();
		const registered = await page.evaluate(
			({ group, tint, spotlight }) => {
				const editors = (window as any).__potionui.editors;
				return {
					group: editors.railGroups.get('image', group)?.label ?? null,
					toolGroup: editors.railTools.get('image', tint)?.group ?? null,
					overlay: editors.overlays.get('image', spotlight)?.produces ?? null
				};
			},
			{ group: GROUP, tint: TINT, spotlight: SPOTLIGHT }
		);
		expect(registered).toEqual({ group: 'Stylize', toolGroup: GROUP, overlay: 'selection' });
		await screenshot(page, JOURNEY, 'rail-1440');

		await rail(page, 'edit_region').click();
		const marking = page.getByLabel('Marking tool');
		await expect(marking.getByText('Spotlight', { exact: true })).toBeVisible();
		await marking.getByText('Spotlight', { exact: true }).click();
		await expect(page.locator('[data-e2e-slot-overlay="spotlight"]')).toBeVisible();
		await screenshot(page, JOURNEY, 'marking-tool-1440');
	});

	test('its task is offered as a rail tool in the Generate group', async ({ page }) => {
		await begin(page, true);
		await openPicture(page);

		const registered = await page.evaluate((relight) => (window as any).__potionui.editors.tasks.get('image', relight)?.label ?? null, RELIGHT);
		expect(registered).toBe('Relight');
		await expect(rail(page, RELIGHT)).toBeVisible();
	});

	test('opening its tool does not loop the editor', async ({ page }) => {
		const errors = watchErrors(page);
		await begin(page, true);
		await openPicture(page);

		await openTint(page);
		await expect(page.locator('[data-image-editor]')).toBeVisible();
		expect(errors).toEqual([]);
	});

	test('a tool that begins its step as it opens can change it and apply it', async ({ page }) => {
		await begin(page, true);
		await openPicture(page);

		await openTint(page);
		await page.locator('[data-e2e-slot-options="Tint"]').getByRole('spinbutton', { name: 'Tint amount' }).fill('70');
		await expect(page.locator('[data-stack-row="pending"]')).toContainText('Strength 70');
		await page.locator('[data-options-apply]').click();
		await expect(page.locator('[data-edit-stack="panel"] [data-stack-row="s1"], [data-edit-stack="panel"] [data-status="current"]')).toContainText('Strength 70');
		await expect(page.locator('[data-stack-row="pending"]')).toHaveCount(0);
	});

	test('its step is added to the stack with its summary, autosaves and survives a reload', async ({ page }) => {
		await begin(page, true);
		await openPicture(page);

		await applyTint(page);
		await screenshot(page, JOURNEY, 'tint-step-1440');
		await expect
			.poll(async () => (await savedSteps(page)).map((step) => ({ tool: step.tool, amount: step.params?.amount })), { timeout: 15000 })
			.toEqual([{ tool: TINT_LATER, amount: 50 }]);

		await page.reload();
		await expect(page.locator('[data-image-editor]')).toBeVisible({ timeout: 30000 });
		await expect(stackRow(page, 'Tint')).toContainText('Strength 50', { timeout: 20000 });
		await expect(stackRow(page, 'Tint').locator('[data-stack-state]')).toHaveCount(0);
		await expect(rail(page, TINT_LATER)).toBeVisible();
		await screenshot(page, JOURNEY, 'reloaded-1440');
	});

	test('with the plugin gone the saved step reads Not available', async ({ page }) => {
		await begin(page, true);
		await openPicture(page);
		await applyTint(page);
		await expect.poll(async () => (await savedSteps(page)).length, { timeout: 15000 }).toBe(1);

		await setPlugin(page, false);
		await page.reload();
		await expect(page.locator('[data-image-editor]')).toBeVisible({ timeout: 30000 });

		await expect(stackRow(page, 'Tint').locator('[data-stack-state="skipped"]')).toHaveText('Not available', { timeout: 20000 });
		await screenshot(page, JOURNEY, 'plugin-absent-1440');
	});

	test('with the plugin gone the saved step stays in the stack and is sent hidden on Save', async ({ page }) => {
		await begin(page, true);
		await openPicture(page);
		await applyCrop(page);
		await applyTint(page);
		await expect.poll(async () => (await savedSteps(page)).length, { timeout: 15000 }).toBe(2);

		await setPlugin(page, false);
		const runs: Json[] = [];
		page.on('request', (request: Request) => {
			if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/media-tools/runs') runs.push(request.postDataJSON());
		});
		await page.reload();
		await expect(page.locator('[data-image-editor]')).toBeVisible({ timeout: 30000 });

		await expect(page.locator('[data-edit-stack="panel"] [data-stack-row]:not([data-stack-row="original"])')).toHaveCount(2, { timeout: 20000 });
		await expect(rail(page, TINT_LATER)).toHaveCount(0);

		const saved = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/media-tools/runs' && response.request().method() === 'POST');
		await page.locator('[data-frame-save]').click();
		const response = await saved;
		expect(response.status(), 'Save must still be accepted when a step belongs to a plugin that is off').toBeLessThan(400);
		expect(runs).toHaveLength(1);
		const steps = runs[0].steps as Json[];
		expect(steps.map((step) => step.tool)).toEqual(['crop', TINT_LATER]);
		expect(steps[0].hidden).toBeFalsy();
		expect(steps[1]).toEqual({ tool: TINT_LATER, params: {}, hidden: true });
		expect((await savedSteps(page)).map((step) => step.tool)).toEqual(['crop', TINT_LATER]);
	});

	test('a plugin step module that throws leaves the editor working', async ({ page }) => {
		await begin(page, true);
		await openPicture(page);

		await rail(page, FAULTY).click();
		await expect(page.locator('[data-e2e-slot-options="Faulty"]')).toBeVisible();
		await expect(page.locator('[data-stack-row="pending"]')).toContainText('Strength 10');
		await page.locator('[data-options-apply]').click();
		await expect(page.locator('[data-stack-row="pending"]')).toHaveCount(0);
		await expect(stackRow(page, 'Faulty').locator('[data-stack-state="skipped"]')).toHaveText('Could not run');
		await expect(page.locator('[data-stack-row="pending"]')).toHaveCount(0);
		await expect(page.locator('[data-image-editor]')).toBeVisible();
		await expect(page.locator('[data-render-error]')).toHaveCount(0);
		await screenshot(page, JOURNEY, 'faulty-step-1440');

		await applyCrop(page);
		await applyTint(page);
		await expect(page.locator('[data-edit-stack="panel"] [data-stack-row]:not([data-stack-row="original"])')).toHaveCount(3);
		await expect(page.locator('[data-render-error]')).toHaveCount(0);
		await expect(rail(page, 'crop')).toBeVisible();
		await expect(rail(page, TINT_LATER)).toBeVisible();
	});

	test('its menu items show up in the Tools menu and in a menu of its own, and run', async ({ page }) => {
		await begin(page, true);
		await openPicture(page);
		await page.evaluate(() => {
			(window as any).__e2eMenuNote = 0;
		});
		const registered = await page.evaluate(
			({ note, shelf }) => {
				const items = (window as any).__potionui.editors.menuItems;
				return { note: items.get('image', note)?.menu ?? null, shelf: items.get('image', shelf)?.menuLabel ?? null };
			},
			{ note: `${PLUGIN}.note`, shelf: `${PLUGIN}.shelf` }
		);
		expect(registered).toEqual({ note: 'tools', shelf: 'Shelf' });
		await expect(page.locator('[data-editor-menubar] [data-menu-tab]')).toHaveText(['File', 'Edit', 'View', 'Tools', 'Shelf']);
		const note = await editorMenuItem(page, 'Tools', 'Tint note');
		await expect(note).toBeVisible();
		await screenshot(page, JOURNEY, 'menu-tools-1440');
		await note.click();
		await expect.poll(() => page.evaluate(() => (window as any).__e2eMenuNote)).toBe(1);
		await editorMenu(page, 'Shelf', 'Shelf item');
		await expect.poll(() => page.evaluate(() => (window as any).__e2eMenuNote)).toBe(2);
		await expect(page.locator('[data-image-editor]')).toBeVisible();
	});

	test('with the plugin off its menu and menu items are gone', async ({ page }) => {
		await begin(page, false);
		await openPicture(page);
		await expect(page.locator('[data-editor-menubar] [data-menu-tab]')).toHaveText(['File', 'Edit', 'View', 'Tools']);
		await openEditorMenu(page, 'Tools');
		await expect(page.locator('[data-editor-menu="tools"] [data-menu-row]').filter({ hasText: 'Tint note' })).toHaveCount(0);
	});
});
