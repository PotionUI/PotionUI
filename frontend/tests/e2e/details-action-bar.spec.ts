import { test, expect, type Locator, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { fixturePng } from './imageEditorHelpers';

const JOURNEY = 'details-action-bar';

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
	tool('crop', 'Crop', { icon: 'crop', accepts: ['image', 'video'], editor: 'crop' }),
	tool('resize', 'Resize', { icon: 'shrink', accepts: ['image', 'video'], editor: 'resize' }),
	tool('rotate', 'Rotate', { accepts: ['image', 'video'] }),
	tool('crop_subject', 'Crop to subject', { accepts: ['image'], requires: { has_alpha: true } }),
	tool('a1111-metadata-export.export-zip', 'Download with A1111 metadata', {
		icon: 'download',
		accepts: ['image'],
		selection: { min: 1 },
		scopes: ['history'],
		component: 'plugin:a1111-metadata-export:A1111ExportTool.js',
		runnable: false,
		source: 'a1111-metadata-export'
	})
];

function generation(id: string, kind: 'image' | 'video', index: number) {
	return {
		id,
		preset_id: 'preset-a',
		preset_name: 'Landscape',
		status: 'completed',
		progress: 1,
		created_at: `2026-01-0${3 - index}T00:00:00Z`,
		updated_at: `2026-01-0${3 - index}T00:00:00Z`,
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

function libraryRow(id: string, name: string, favorite = false) {
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
		is_favorite: favorite,
		has_alpha: false
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
	const writes: Array<{ id: string; is_favorite: boolean }> = [];
	const rows = [libraryRow('lib-1', 'harbour.png'), libraryRow('lib-2', 'dunes.png', true)];
	const generations = [generation('gen-1', 'image', 1), generation('gen-2', 'video', 2)];

	await page.route(/\/api\/media-tools(\?.*)?$/, (route) => json(route, { tools: CATALOG }));
	await page.route(/\/api\/library\/items(\?.*)?$/, (route) =>
		json(route, { items: rows, total: rows.length, limit: 24, offset: 0 })
	);
	await page.route(/\/api\/library\/items\/[^/]+\/favorite$/, async (route) => {
		const id = route.request().url().split('/').slice(-2)[0];
		const body = route.request().postDataJSON() as { is_favorite: boolean };
		writes.push({ id, is_favorite: body.is_favorite });
		await json(route, { id, is_favorite: body.is_favorite });
	});
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
		json(route, { generations, total: generations.length })
	);
	await page.route(/\/api\/tags(\?.*)?$/, (route) => json(route, { tags: [] }));
	await page.route(/\/api\/media\/generations\/gen-\d+\/.+$/, (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PNG })
	);
	return { writes };
}

const bar = (root: Page | Locator) => root.getByRole('toolbar', { name: 'Actions' });
const barIds = (root: Page | Locator) =>
	bar(root).locator('[data-bar-item]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-bar-item')));
const rowLabels = (menuEl: Locator) => menuEl.locator('[data-bar-row-label]').allTextContents();

async function openHistoryDetails(page: Page, index = 0) {
	await page.goto('/history');
	const cards = page.locator('[data-generation-card]');
	await expect(cards).toHaveCount(2);
	const dialog = page.getByRole('dialog', { name: 'Generation Details' });
	for (let position = 0; position < 2; position += 1) {
		await cards.nth(position).locator('.media-zoom').click();
		await expect(dialog).toBeVisible();
		if ((await dialog.locator('code').first().innerText()).trim() === `gen-${index + 1}`) break;
		await page.keyboard.press('Escape');
		await expect(dialog).toHaveCount(0);
	}
	await expect(bar(page)).toBeVisible();
}

async function openLibraryDetails(page: Page, name = 'harbour.png') {
	await page.goto('/library');
	await page.locator('[data-library-card]').filter({ hasText: name }).locator('.media-zoom').click();
	await expect(bar(page)).toBeVisible();
}

async function openMenu(page: Page, name: string) {
	await bar(page).getByRole('button', { name }).click();
	const menuEl = page.getByRole('menu', { name });
	await expect(menuEl).toBeVisible();
	return menuEl;
}

test.describe('the bar on History', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
		await mockBackend(page);
	});

	test('shows every action, labelled, between the header and the media', async ({ page }) => {
		await openHistoryDetails(page);

		expect(await barIds(page)).toEqual([
			'edit',
			'effects',
			'favorite',
			'tags',
			'reuse',
			'copy-to-library',
			'download',
			'more'
		]);
		const dialog = page.getByRole('dialog', { name: 'Generation Details' });
		const headerBox = (await dialog.getByRole('button', { name: 'Close' }).first().boundingBox())!;
		const barBox = (await bar(page).boundingBox())!;
		const stageBox = (await page.locator('[data-details-stage]').boundingBox())!;
		expect(barBox.y).toBeGreaterThan(headerBox.y);
		expect(barBox.y + barBox.height).toBeLessThanOrEqual(stageBox.y + 1);
		await expect(dialog.locator('[data-stage-toolbar]')).toHaveCount(0);
		await screenshot(page, JOURNEY, 'history-rest-1440');
	});

	test('keeps one tab stop and moves with the arrow keys', async ({ page }) => {
		await openHistoryDetails(page);
		const items = bar(page).locator('[data-bar-item]');

		expect(await items.evaluateAll((nodes) => nodes.filter((node) => (node as HTMLElement).tabIndex === 0).length)).toBe(1);
		await items.first().focus();
		await page.keyboard.press('ArrowRight');
		await expect(bar(page).getByRole('button', { name: 'Effects' })).toBeFocused();
		await page.keyboard.press('End');
		await expect(bar(page).getByRole('button', { name: 'More' })).toBeFocused();
		await page.keyboard.press('ArrowRight');
		await expect(bar(page).getByRole('button', { name: 'Edit' })).toBeFocused();
	});

	test('opens a menu with ArrowDown, lands on the first enabled row and closes on Escape with focus back', async ({
		page
	}) => {
		await openHistoryDetails(page);
		const edit = bar(page).getByRole('button', { name: 'Edit' });
		await edit.focus();

		await page.keyboard.press('ArrowDown');
		const menuEl = page.getByRole('menu', { name: 'Edit' });
		await expect(menuEl).toBeVisible();
		await expect(menuEl.locator('[data-bar-row="crop"]')).toBeFocused();
		await screenshot(page, JOURNEY, 'history-edit-1440');

		await page.keyboard.press('Escape');
		await expect(menuEl).toHaveCount(0);
		await expect(edit).toBeFocused();
	});

	test('closes a menu on Tab, and on Right moves to the neighbouring button', async ({ page }) => {
		await openHistoryDetails(page);
		const edit = bar(page).getByRole('button', { name: 'Edit' });
		await edit.focus();
		await page.keyboard.press('Enter');
		await expect(page.getByRole('menu', { name: 'Edit' })).toBeVisible();

		await page.keyboard.press('ArrowRight');
		await expect(page.getByRole('menu', { name: 'Edit' })).toHaveCount(0);
		await expect(bar(page).getByRole('button', { name: 'Effects' })).toBeFocused();

		await page.keyboard.press('Enter');
		await expect(page.getByRole('menu', { name: 'Effects' })).toBeVisible();
		await page.keyboard.press('Tab');
		await expect(page.getByRole('menu', { name: 'Effects' })).toHaveCount(0);
		await expect(bar(page).getByRole('button', { name: 'Effects' })).not.toBeFocused();
	});

	test('names the plugin on the A1111 download', async ({ page }) => {
		await openHistoryDetails(page);

		const download = await openMenu(page, 'Download');
		expect(await rowLabels(download)).toEqual(['Download file', 'Export bundle', 'Download with A1111 metadata']);
		await expect(download.locator('[data-bar-row="a1111-metadata-export.export-zip"]')).toContainText('Plugin');
		await expect(download.getByRole('separator')).toHaveCount(1);
		await screenshot(page, JOURNEY, 'history-download-1440');
	});

	test('leaves the A1111 export out of a video Download menu and has no Effects for it', async ({ page }) => {
		await openHistoryDetails(page, 1);

		expect(await barIds(page)).not.toContain('effects');
		const download = await openMenu(page, 'Download');
		expect(await rowLabels(download)).toEqual(['Download file', 'Export bundle']);
		await page.keyboard.press('Escape');
		const edit = await openMenu(page, 'Edit');
		expect(await rowLabels(edit)).toEqual(['Trim', 'Crop', 'Resize', 'Rotate', 'Open in Video editor']);
	});

	test('folds Download, Copy to Library and Reuse settings into More on a 1024 window', async ({ page }) => {
		await page.setViewportSize({ width: 1024, height: 768 });
		await openHistoryDetails(page);

		expect(await barIds(page)).toEqual(['edit', 'effects', 'favorite', 'tags', 'more']);
		const more = await openMenu(page, 'More');
		expect(await rowLabels(more)).toEqual([
			'Reuse settings',
			'Copy to Library',
			'Download file',
			'Export bundle',
			'Download with A1111 metadata',
			'Publish to Inspirations',
			'Open in new tab'
		]);
		await screenshot(page, JOURNEY, 'history-narrow-more-1024');
	});

	test('folds Tags, then Favorite, and keeps Edit, Effects and More down to 320px', async ({ page }) => {
		await page.setViewportSize({ width: 390, height: 800 });
		await openHistoryDetails(page);
		expect(await barIds(page)).toEqual(['edit', 'effects', 'favorite', 'more']);
		await page.setViewportSize({ width: 320, height: 800 });
		await expect.poll(() => barIds(page)).toEqual(['edit', 'effects', 'more']);
		const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
		expect(scrollWidth).toBeLessThanOrEqual(320);
	});

	test('opens menus as a bottom sheet on a phone', async ({ page }) => {
		await page.setViewportSize({ width: 390, height: 800 });
		await openHistoryDetails(page);

		await bar(page).getByRole('button', { name: 'More' }).click();
		const sheet = page.locator('[data-bar-sheet]');
		await expect(sheet).toBeVisible();
		const box = (await sheet.boundingBox())!;
		expect(box.y + box.height).toBeGreaterThanOrEqual(799);
		await screenshot(page, JOURNEY, 'history-more-sheet-390');
		await page.keyboard.press('Escape');
		await expect(sheet).toHaveCount(0);
	});

	test('shows Favorite off for a generation that is not a favorite', async ({ page }) => {
		await openHistoryDetails(page);
		const favorite = bar(page).getByRole('button', { name: 'Favorite' });

		await expect(favorite).toHaveAttribute('aria-pressed', 'false');
	});
});

test.describe('the bar on the Library', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
	});

	test('shows Auto-file, a plain Download, and Favorite that writes', async ({ page }) => {
		const backend = await mockBackend(page);
		await openLibraryDetails(page);

		expect(await barIds(page)).toEqual(['edit', 'effects', 'favorite', 'tags', 'auto-file', 'download', 'more']);
		await expect(bar(page).getByRole('button', { name: 'Download' })).not.toHaveAttribute('aria-haspopup', 'menu');
		const favorite = bar(page).getByRole('button', { name: 'Favorite' });
		await favorite.click();
		await expect(favorite).toHaveAttribute('aria-pressed', 'true');
		await expect.poll(() => backend.writes).toEqual([{ id: 'lib-1', is_favorite: true }]);
		await screenshot(page, JOURNEY, 'library-rest-1440');
	});

	test('puts Open in new tab and a red Delete last in More', async ({ page }) => {
		await mockBackend(page);
		await openLibraryDetails(page);

		const more = await openMenu(page, 'More');
		expect(await rowLabels(more)).toEqual(['Open in new tab', 'Delete']);
		await expect(more.locator('[data-bar-row="delete"]')).toHaveClass(/text-danger/);
		await more.locator('[data-bar-row="delete"]').click();
		await expect(page.getByRole('alertdialog')).toBeVisible();
	});

	test('disables Crop to subject for an opaque upload with its reason', async ({ page }) => {
		await mockBackend(page);
		await openLibraryDetails(page);

		const edit = await openMenu(page, 'Edit');
		await expect(edit.locator('[data-bar-row="crop_subject"]')).toContainText('Needs a transparent image');
		await screenshot(page, JOURNEY, 'library-edit-1440');
	});

	test('keeps the selected tags as chips under the media, not in the corner', async ({ page }) => {
		await mockBackend(page);
		await openLibraryDetails(page);

		await expect(page.locator('[data-library-item-tags]')).toBeVisible();
	});
});

test.describe('grid cards', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
	});

	test('the History card corner holds the checkbox, a Favorite toggle and the menu chip', async ({ page }) => {
		await mockBackend(page);
		await page.goto('/history');
		const card = page.locator('[data-generation-card]').first();
		await card.hover();

		const names = await card
			.getByRole('button')
			.evaluateAll((nodes) => nodes.map((node) => node.getAttribute('aria-label') ?? ''));
		expect(names).toEqual(expect.arrayContaining(['Select generation', 'Favorite', 'Item actions']));
		expect(names).not.toContain('Delete generation');
		expect(names).not.toContain('View generation details');
		await card.getByRole('button', { name: 'Item actions' }).click();
		const menuEl = page.locator('[data-tools-menu]');
		await expect(menuEl.locator('[data-tool="delete"]')).toHaveClass(/text-danger/);
		await screenshot(page, JOURNEY, 'cards-history-1440');
	});

	test('the Library card gains the same Favorite toggle, on for a favorite', async ({ page }) => {
		const backend = await mockBackend(page);
		await page.goto('/library');
		const card = page.locator('[data-library-card]').filter({ hasText: 'dunes.png' });
		await expect(card.getByRole('button', { name: 'Remove favorite' })).toHaveAttribute('aria-pressed', 'true');

		const other = page.locator('[data-library-card]').filter({ hasText: 'harbour.png' });
		await other.hover();
		await other.getByRole('button', { name: 'Favorite' }).click();
		await expect.poll(() => backend.writes).toEqual([{ id: 'lib-1', is_favorite: true }]);
		await screenshot(page, JOURNEY, 'cards-library-1440');
	});
});

test.describe('letter shortcuts', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
	});

	const imageEditorTab = (page: Page) => page.getByTestId('tool-tab-host');

	test('E in the History details closes them and opens an Image editor tab', async ({ page }) => {
		await mockBackend(page);
		await openHistoryDetails(page);

		await page.keyboard.press('e');

		await expect(page.getByRole('dialog', { name: 'Generation Details' })).toHaveCount(0);
		await expect(page).toHaveURL(/\/generate/);
		await expect(imageEditorTab(page)).toHaveAttribute('data-tool', 'image');
	});

	test('F in the Library details toggles the favorite and E opens an Image editor tab', async ({ page }) => {
		const backend = await mockBackend(page);
		await openLibraryDetails(page);

		await page.keyboard.press('f');
		await expect.poll(() => backend.writes).toEqual([{ id: 'lib-1', is_favorite: true }]);
		await expect(bar(page).getByRole('button', { name: 'Favorite' })).toHaveAttribute('aria-pressed', 'true');

		await page.keyboard.press('e');
		await expect(page).toHaveURL(/\/generate/);
		await expect(imageEditorTab(page)).toHaveAttribute('data-tool', 'image');
	});

	test('the letters do nothing while a bar menu is open above the details', async ({ page }) => {
		const backend = await mockBackend(page);
		await openLibraryDetails(page);
		await openMenu(page, 'More');

		await page.keyboard.press('f');

		expect(backend.writes).toEqual([]);
	});

	test('E on the History grid opens an Image editor tab for the one selected image', async ({ page }) => {
		await mockBackend(page);
		await page.goto('/history');
		const cards = page.locator('[data-generation-card]');
		await expect(cards).toHaveCount(2);
		const target = page.locator('[data-generation-card][data-generation-id="gen-1"]');
		await target.hover();
		await target.getByRole('button', { name: 'Select generation' }).click();

		await page.keyboard.press('e');

		await expect(page).toHaveURL(/\/generate/);
		await expect(imageEditorTab(page)).toHaveAttribute('data-tool', 'image');
	});

	test('E on the Library grid opens an Image editor tab for the one selected image', async ({ page }) => {
		await mockBackend(page);
		await page.goto('/library');
		const card = page.locator('[data-library-card]').filter({ hasText: 'harbour.png' });
		await card.hover();
		await card.getByRole('button', { name: 'Select item' }).click();

		await page.keyboard.press('e');

		await expect(page).toHaveURL(/\/generate/);
		await expect(imageEditorTab(page)).toHaveAttribute('data-tool', 'image');
	});

	test('E in an open card menu runs the Open in Image editor row', async ({ page }) => {
		await mockBackend(page);
		await page.goto('/library');
		const card = page.locator('[data-library-card]').filter({ hasText: 'harbour.png' });
		await card.hover();
		await card.getByRole('button', { name: 'Item actions' }).click();
		await expect(page.locator('[data-tools-menu]')).toBeVisible();

		await page.keyboard.press('e');

		await expect(page).toHaveURL(/\/generate/);
		await expect(imageEditorTab(page)).toHaveAttribute('data-tool', 'image');
	});
});
