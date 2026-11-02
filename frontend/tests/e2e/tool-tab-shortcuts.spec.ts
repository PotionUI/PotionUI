import { test, expect, type Locator, type Page, type Route } from '@playwright/test';
import { loginAsOwner, scopedStorageKey, screenshot } from './helpers';
import { fixturePng, openControlTab, prepare } from './imageEditorHelpers';
import { pickFieldTool } from './mediaFieldHelpers';

const JOURNEY = 'tool-tab-shortcuts';

const PNG = fixturePng(96);
const PNG_URL = `data:image/png;base64,${PNG.toString('base64')}`;

function tool(id: string, label: string, extra: Record<string, unknown> = {}) {
	return {
		id,
		label,
		icon: 'sliders',
		accepts: ['image'],
		produces: 'same',
		cardinality: 'one_to_one',
		params: [],
		requires: {},
		selection: null,
		scopes: ['library', 'history', 'field'],
		editor: null,
		preview: false,
		component: null,
		runnable: true,
		source: 'core',
		last_params: null,
		...extra
	};
}

const CATALOG = [
	tool('trim', 'Trim', { icon: 'scissors', accepts: ['video', 'audio'], editor: 'trim' }),
	tool('split', 'Split', { icon: 'scissors', accepts: ['video', 'audio'], editor: 'split' }),
	tool('crop', 'Crop', { icon: 'crop', accepts: ['image', 'video'], editor: 'crop' }),
	tool('resize', 'Resize', { icon: 'shrink', accepts: ['image', 'video'], editor: 'resize' }),
	tool('rotate', 'Rotate', { icon: 'refresh', accepts: ['image', 'video'] }),
	tool('flip', 'Flip', { icon: 'refresh', accepts: ['image', 'video'] }),
	tool('speed', 'Speed', { icon: 'gauge', accepts: ['video', 'audio'] }),
	tool('fit_canvas', 'Fit canvas', { icon: 'expand', accepts: ['image'] }),
	tool('color_key', 'Color key', { icon: 'palette', accepts: ['image'] }),
	tool('crop_subject', 'Crop to subject', { icon: 'crop', accepts: ['image'], requires: { has_alpha: true } }),
	tool('volume', 'Volume', { icon: 'volume', accepts: ['video', 'audio'], group: 'audio' }),
	tool('mute', 'Mute', { icon: 'volume', accepts: ['video'], group: 'audio' }),
	tool('frame', 'Frame', { icon: 'photo', accepts: ['video'], produces: 'image', editor: 'frame' }),
	tool('film_grain', 'Film grain', { icon: 'film', accepts: ['video'], group: 'effects' })
];

function generation(id: string, kind: 'image' | 'video', index: number) {
	return {
		id,
		preset_id: 'preset-a',
		preset_name: kind === 'image' ? 'Krea 2' : 'LTX 2',
		status: 'completed',
		progress: 1,
		created_at: `2026-01-0${5 - index}T00:00:00Z`,
		updated_at: `2026-01-0${5 - index}T00:00:00Z`,
		form_data: { prompt: `prompt for ${id}` },
		files: [
			{
				id: index,
				file_path: `out/${id}/clip.${kind === 'image' ? 'png' : 'mp4'}`,
				file_type: kind,
				is_final: true,
				created_at: '2026-01-01T00:00:00Z',
				width: 96,
				height: 96,
				duration_seconds: kind === 'video' ? 8 : undefined,
				thumbnail_small: PNG_URL,
				thumbnail_medium: PNG_URL,
				thumbnail_large: PNG_URL
			}
		],
		rating: 0,
		is_favorite: false
	};
}

function libraryRow(id: string, name: string) {
	return {
		id,
		filename: `${id}-file.png`,
		original_filename: name,
		media_type: 'image',
		url: PNG_URL,
		thumbnail_medium: PNG_URL,
		width: 96,
		height: 96,
		created_at: '2026-01-01T00:00:00Z',
		tags: [],
		is_favorite: false,
		has_alpha: true
	};
}

async function json(route: Route, data: unknown) {
	await route.fulfill({
		status: 200,
		contentType: 'application/json',
		body: JSON.stringify({ success: true, data })
	});
}

async function mockBackend(page: Page) {
	const generations = [
		generation('gen-1', 'image', 1),
		generation('gen-2', 'video', 2),
		generation('gen-3', 'image', 3)
	];
	const rows = [libraryRow('lib-1', 'harbour.png')];
	await page.route(/\/api\/media-tools(\?.*)?$/, (route) => json(route, { tools: CATALOG }));
	await page.route(/\/api\/library\/items(\?.*)?$/, (route) =>
		json(route, { items: rows, total: rows.length, limit: 24, offset: 0 })
	);
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
		json(route, { generations, total: generations.length })
	);
	await page.route(/\/api\/tags(\?.*)?$/, (route) => json(route, { tags: [] }));
	await page.route(/\/api\/media\/generations\/gen-\d+\/.+$/, (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PNG })
	);
}

interface SavedToolTab {
	tool: string;
	activeTool: string | null;
	source: { url?: string; generation_file_id?: string; item_id?: string; name?: string } | null;
}

async function savedToolTabs(page: Page): Promise<SavedToolTab[]> {
	const key = await scopedStorageKey(page, 'potionui_tabs_state');
	return page.evaluate((storageKey) => {
		const saved = JSON.parse(localStorage.getItem(storageKey) ?? '{"tabs":[]}');
		return saved.tabs.filter((tab: { kind?: string }) => tab.kind === 'tool');
	}, key);
}

async function activeTabId(page: Page): Promise<string> {
	const key = await scopedStorageKey(page, 'potionui_tabs_state');
	return page.evaluate((storageKey) => JSON.parse(localStorage.getItem(storageKey) ?? '{}').activeTabId ?? '', key);
}

const bar = (root: Page | Locator) => root.getByRole('toolbar', { name: 'Actions' });
const host = (page: Page) => page.getByTestId('tool-tab-host');
const bookTab = (page: Page, name: string) => page.locator('button.book-tab', { hasText: name });

async function openHistoryDetails(page: Page, generationId: string) {
	const card = page.locator(`[data-generation-card][data-generation-id="${generationId}"]`);
	await expect(card).toBeVisible();
	await card.locator('.media-zoom').click();
	const dialog = page.getByRole('dialog', { name: 'Generation Details' });
	await expect(dialog).toBeVisible();
	await expect(dialog.locator('code').first()).toHaveText(generationId);
	await expect(bar(page)).toBeVisible();
	return dialog;
}

async function openMenu(page: Page, name: string) {
	await bar(page).getByRole('button', { name }).click();
	const menu = page.getByRole('menu', { name });
	await expect(menu).toBeVisible();
	return menu;
}

async function pickRow(menu: Locator, label: string) {
	await menu.getByRole('menuitem', { name: label, exact: true }).click();
}

async function goToHistoryInApp(page: Page) {
	await page.locator('a[href="/history"]').first().click();
	await expect(page).toHaveURL(/\/history/);
	await expect(page.locator('[data-generation-card]').first()).toBeVisible();
}

test.describe('Edit shortcuts on desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test.beforeEach(async ({ page }) => {
		await loginAsOwner(page);
		await mockBackend(page);
		await page.goto('/history');
	});

	test('History image: Crop opens an Image editor tab with Crop active, and the details close', async ({ page }) => {
		const dialog = await openHistoryDetails(page, 'gen-1');
		const menu = await openMenu(page, 'Edit');
		await expect(menu.locator('[data-bar-row="crop"] [data-opens-elsewhere]')).toBeVisible();
		await expect(menu.locator('[data-tab-footer]')).toContainText('Image editor tab');
		await screenshot(page, JOURNEY, 'history-image-edit-menu-1440');

		await pickRow(menu, 'Crop');

		await expect(dialog).toHaveCount(0);
		await expect(page).toHaveURL(/\/generate/);
		await expect(host(page)).toHaveAttribute('data-tool', 'image');
		await expect(page.getByTestId('tool-tab-source')).toBeVisible();
		await expect.poll(async () => (await savedToolTabs(page)).map((tab) => [tab.tool, tab.activeTool])).toEqual([['image', 'crop']]);
		const [saved] = await savedToolTabs(page);
		expect(saved.source?.url).toContain('/api/media/generations/gen-1/');
		expect(saved.source?.generation_file_id).toBe('1');
		await screenshot(page, JOURNEY, 'image-editor-tab-1440');
	});

	test('History video: Trim opens the Video editor with Trim active', async ({ page }) => {
		await openHistoryDetails(page, 'gen-2');
		const menu = await openMenu(page, 'Edit');
		await expect(menu.locator('[data-tab-footer]')).toContainText('Video editor tab');
		await expect(menu.getByRole('menuitem', { name: 'Open frame in Image editor' })).toBeVisible();
		await expect(menu.getByRole('menuitem', { name: 'Audio' })).toBeVisible();
		await expect(menu.getByRole('menuitem', { name: 'Mute' })).toHaveCount(0);
		await screenshot(page, JOURNEY, 'history-video-edit-menu-1440');

		await pickRow(menu, 'Trim');

		await expect(page).toHaveURL(/\/generate/);
		await expect(host(page)).toHaveAttribute('data-tool', 'video');
		await expect.poll(async () => (await savedToolTabs(page)).map((tab) => [tab.tool, tab.activeTool])).toEqual([['video', 'trim']]);
	});

	test('a video effect and a frame open the Video editor on that tool', async ({ page }) => {
		await openHistoryDetails(page, 'gen-2');
		await pickRow(await openMenu(page, 'Effects'), 'Film grain');
		await expect.poll(async () => (await savedToolTabs(page)).map((tab) => [tab.tool, tab.activeTool])).toEqual([['video', 'effects']]);

		await goToHistoryInApp(page);
		await openHistoryDetails(page, 'gen-2');
		await pickRow(await openMenu(page, 'Edit'), 'Open frame in Image editor');

		await expect.poll(async () => (await savedToolTabs(page)).map((tab) => [tab.tool, tab.activeTool])).toEqual([['video', 'frame']]);
	});

	test('a look opens the Image editor on Effects', async ({ page }) => {
		await openHistoryDetails(page, 'gen-1');
		await pickRow(await openMenu(page, 'Effects'), 'Apply a look…');

		await expect.poll(async () => (await savedToolTabs(page)).map((tab) => [tab.tool, tab.activeTool])).toEqual([['image', 'effects']]);
	});

	test('a selection of two images opens two tabs with the first active, and the grid keeps its selection', async ({ page }) => {
		for (const id of ['gen-1', 'gen-3']) {
			const card = page.locator(`[data-generation-card][data-generation-id="${id}"]`);
			await card.hover();
			await card.getByRole('button', { name: 'Select generation' }).click();
		}
		await page.getByRole('button', { name: /^Tools/ }).click();
		const menu = page.locator('[data-tools-menu]');
		await menu.locator('[data-submenu-row="edit"]').click();
		await expect(menu.locator('[data-tab-footer]')).toContainText('one Image editor tab per picture');
		await screenshot(page, JOURNEY, 'history-selection-tools-menu-1440');
		await menu.locator('[data-tool="crop"]').click();

		await expect(page).toHaveURL(/\/generate/);
		await expect.poll(async () => (await savedToolTabs(page)).length).toBe(2);
		const tabs = await savedToolTabs(page);
		expect(tabs.map((tab) => [tab.tool, tab.activeTool])).toEqual([
			['image', 'crop'],
			['image', 'crop']
		]);
		expect(tabs.map((tab) => tab.source?.url ?? '').map((url) => /gen-\d/.exec(url)?.[0])).toEqual(['gen-1', 'gen-3']);
		await expect.poll(async () => (await activeTabId(page)).length).toBeGreaterThan(0);
		await expect(host(page)).toHaveAttribute('data-tool', 'image');
		await expect(page.locator('button.book-tab').filter({ has: page.getByTestId('tab-tool-thumb') })).toHaveCount(2);

		await goToHistoryInApp(page);
		await expect(page.locator('[data-generation-card] [aria-label="Deselect generation"]')).toHaveCount(2);
	});

	test('a Library item: Crop opens the Image editor on that item', async ({ page }) => {
		await page.goto('/library');
		await page.locator('[data-library-card]').filter({ hasText: 'harbour.png' }).locator('.media-zoom').click();
		await expect(bar(page)).toBeVisible();
		const menu = await openMenu(page, 'Edit');
		await expect(menu.locator('[data-tab-footer]')).toContainText('Image editor tab');

		await pickRow(menu, 'Crop');

		await expect(page).toHaveURL(/\/generate/);
		await expect.poll(async () => (await savedToolTabs(page)).map((tab) => [tab.tool, tab.activeTool])).toEqual([['image', 'crop']]);
		expect((await savedToolTabs(page))[0].source?.item_id).toBe('lib-1');
	});

	test('E opens Trim for a video and Paint for an image the first time', async ({ page }) => {
		await openHistoryDetails(page, 'gen-2');
		await page.keyboard.press('e');
		await expect(host(page)).toHaveAttribute('data-tool', 'video');
		await expect.poll(async () => (await savedToolTabs(page)).map((tab) => [tab.tool, tab.activeTool])).toEqual([['video', 'trim']]);

		await goToHistoryInApp(page);
		await openHistoryDetails(page, 'gen-1');
		await page.keyboard.press('e');

		await expect.poll(async () => (await savedToolTabs(page)).map((tab) => [tab.tool, tab.activeTool])).toEqual([
			['video', 'trim'],
			['image', 'paint']
		]);
	});

	test('E reopens the last tool used, and the memory survives a reload', async ({ page }) => {
		await openHistoryDetails(page, 'gen-1');
		await pickRow(await openMenu(page, 'Edit'), 'Resize');
		await expect.poll(async () => (await savedToolTabs(page)).map((tab) => tab.activeTool)).toEqual(['resize']);

		await page.reload();
		await goToHistoryInApp(page);
		await openHistoryDetails(page, 'gen-3');
		await page.keyboard.press('e');

		await expect(page).toHaveURL(/\/generate/);
		await expect.poll(async () => (await savedToolTabs(page)).map((tab) => [tab.tool, tab.activeTool])).toEqual([
			['image', 'resize'],
			['image', 'resize']
		]);
	});

	test('the field toolbar opens the matching tab on the field media', async ({ page }) => {
		test.setTimeout(240000);
		const name = await prepare(page);
		const root = await openControlTab(page, false, name);
		const field = root.locator('[data-field-name]:has(input[type="file"])').first();
		await field.scrollIntoViewIfNeeded();
		await Promise.all([
			page.waitForResponse((r) => r.url().includes('/api/media/upload') && r.request().method() === 'POST'),
			field.locator('input[type="file"]').first().setInputFiles({
				name: 'sample.png',
				mimeType: 'image/png',
				buffer: fixturePng(128)
			})
		]);
		await expect(field.locator('[data-media-inspector]')).toBeVisible();
		await field.locator('[data-tools-trigger]').first().click();
		await expect(page.locator('[data-tools-menu] [data-tool="open-in-editor"]')).toContainText('Open in Image editor');
		await page.keyboard.press('Escape');

		await pickFieldTool(page, field, 'open-in-editor');

		await expect(host(page)).toHaveAttribute('data-tool', 'image');
		await expect(page.getByTestId('tool-tab-source')).toBeVisible();
		await expect.poll(async () => (await savedToolTabs(page)).map((tab) => [tab.tool, tab.activeTool])).toEqual([['image', 'paint']]);
		expect((await savedToolTabs(page))[0].source?.name).toBe('sample.png');
		await expect(bookTab(page, 'sample')).toBeVisible();
	});
});

test.describe('Edit shortcuts on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('the Edit sheet marks the rows that open a tab and names the editor', async ({ page }) => {
		await loginAsOwner(page);
		await mockBackend(page);
		await page.goto('/history');
		await openHistoryDetails(page, 'gen-1');

		await bar(page).getByRole('button', { name: 'Edit' }).click();
		const sheet = page.locator('[data-bar-sheet]');
		await expect(sheet).toBeVisible();
		await expect(sheet.locator('[data-tab-footer]')).toContainText('Image editor tab');
		await expect(sheet.locator('[data-bar-row="crop"] [data-opens-elsewhere]')).toBeVisible();
		await screenshot(page, JOURNEY, 'history-image-edit-sheet-390');

		await sheet.getByRole('menuitem', { name: 'Crop', exact: true }).click();

		await expect(page.getByTestId('phone-tool-tab')).toBeVisible();
		await expect.poll(async () => (await savedToolTabs(page)).map((tab) => [tab.tool, tab.activeTool])).toEqual([['image', 'crop']]);
		await screenshot(page, JOURNEY, 'image-editor-tab-390');
		const overflowX = await page.evaluate(() => {
			const el = document.scrollingElement!;
			return el.scrollWidth - el.clientWidth;
		});
		expect(overflowX).toBeLessThanOrEqual(0);
	});

	test('the Video editor sheet at 390', async ({ page }) => {
		await loginAsOwner(page);
		await mockBackend(page);
		await page.goto('/history');
		await openHistoryDetails(page, 'gen-2');

		await bar(page).getByRole('button', { name: 'Edit' }).click();
		const sheet = page.locator('[data-bar-sheet]');
		await expect(sheet.locator('[data-tab-footer]')).toContainText('Video editor tab');
		await screenshot(page, JOURNEY, 'history-video-edit-sheet-390');
	});
});
