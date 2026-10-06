import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { expectEditorReady, fixturePng } from './imageEditorHelpers';

const JOURNEY = 'entry-tools-menu';

const PNG = fixturePng(96);
const PNG_URL = `data:image/png;base64,${PNG.toString('base64')}`;

function file(id: number, name: string) {
	return {
		id,
		file_path: `out/${name}`,
		file_type: 'image',
		is_final: true,
		created_at: '2026-01-01T00:00:00Z',
		width: 96,
		height: 96,
		thumbnail_small: PNG_URL,
		thumbnail_medium: PNG_URL,
		thumbnail_large: PNG_URL
	};
}

function generation(id: string, name: string) {
	return {
		id,
		preset_name: 'Landscape',
		status: 'completed',
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		form_data: { prompt: `prompt for ${id}` },
		files: [file(Number(id.split('-')[1]), `${id}/${name}.png`)],
		rating: 0,
		is_favorite: false
	};
}

const LIBRARY = [
	{
		id: 'lib-1',
		filename: 'harbour-file.png',
		original_filename: 'harbour.png',
		media_type: 'image',
		url: PNG_URL,
		thumbnail_medium: PNG_URL,
		width: 96,
		height: 96,
		tags: []
	}
];

async function json(route: Route, data: unknown) {
	await route.fulfill({
		status: 200,
		contentType: 'application/json',
		body: JSON.stringify({ success: true, data })
	});
}

async function mockMedia(page: Page) {
	let generations = [generation('gen-1', 'sunset'), generation('gen-2', 'dunes')];
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
		json(route, { generations, total: generations.length })
	);
	await page.route(/\/api\/generations\/history\/gen-\d+$/, async (route) => {
		if (route.request().method() !== 'DELETE') return route.fallback();
		const id = route.request().url().split('/').pop();
		generations = generations.filter((entry) => entry.id !== id);
		await json(route, { message: 'deleted' });
	});
	await page.route(/\/api\/library\/items(\?.*)?$/, (route) =>
		json(route, { items: LIBRARY, total: LIBRARY.length, limit: 20, offset: 0 })
	);
	await page.route(/\/api\/tags(\?.*)?$/, (route) => json(route, { tags: [] }));
	await page.route(/\/api\/media\/generations\/gen-\d+\/.+\.png$/, (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PNG })
	);
}

test.describe('per-entry tools menu', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await loginAsOwner(page);
		await mockMedia(page);
	});

	test('runs Edit image from the menu of a History card', async ({ page }) => {
		test.setTimeout(120000);
		await page.goto('/history');
		const cards = page.locator('[data-generation-card]');
		await expect(cards).toHaveCount(2);

		const card = cards.first();
		await card.hover();
		await expect(card.locator('[data-entry-actions] button')).toHaveCount(2);
		await card.getByRole('button', { name: 'Item actions' }).click();

		const menu = page.locator('[data-tools-menu]');
		await expect(menu).toBeVisible();
		await expect(menu.locator('[data-tool="open-details"]')).toBeVisible();
		await expect(menu.locator('[data-tool="compare"]')).toHaveCount(0);
		await expect(menu.locator('[data-tool="delete"]')).toBeVisible();
		await screenshot(page, JOURNEY, 'history-menu-1440');

		await menu.locator('[data-tool="edit-image"]').click();
		const dialog = page.getByRole('dialog', { name: 'Edit image' });
		await expect(dialog).toBeVisible();
		await expectEditorReady(dialog.getByLabel('Drawing canvas'));
	});

	test('opens the menu with a right-click', async ({ page }) => {
		await page.goto('/history');
		const card = page.locator('[data-generation-card]').first();
		await expect(card).toBeVisible();
		await card.locator('.media-zoom').click({ button: 'right' });
		await expect(page.locator('[data-tools-menu]')).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(page.locator('[data-tools-menu]')).toHaveCount(0);
	});

	test('deletes a History card after confirming', async ({ page }) => {
		await page.goto('/history');
		const cards = page.locator('[data-generation-card]');
		await expect(cards).toHaveCount(2);

		await cards.first().getByRole('button', { name: 'Item actions' }).click();
		await page.locator('[data-tools-menu] [data-tool="delete"]').click();

		const dialog = page.getByRole('dialog', { name: 'Delete Generation' });
		await expect(dialog).toBeVisible();
		await expect(cards).toHaveCount(2);
		await dialog.getByRole('button', { name: 'Confirm' }).click();

		await expect(cards).toHaveCount(1);
	});

	test('the hover Delete button asks for the same confirmation', async ({ page }) => {
		await page.goto('/history');
		const cards = page.locator('[data-generation-card]');
		await expect(cards).toHaveCount(2);
		await cards.first().hover();
		await cards.first().getByRole('button', { name: 'Delete generation' }).click();
		const dialog = page.getByRole('dialog', { name: 'Delete Generation' });
		await expect(dialog).toBeVisible();
		await dialog.getByRole('button', { name: 'Cancel' }).click();
		await expect(cards).toHaveCount(2);
	});

	test('Library cards carry the same menu and no pill toolbar', async ({ page }) => {
		await page.goto('/library');
		const card = page.locator('[data-library-card-body]').first();
		await expect(card).toBeVisible();
		await card.hover();
		await expect(card.getByRole('button', { name: 'Open library item' })).toHaveCount(0);
		await expect(card.getByRole('button', { name: 'Download' })).toHaveCount(0);
		await card.getByRole('button', { name: 'Item actions' }).click();

		const menu = page.locator('[data-tools-menu]');
		await expect(menu).toBeVisible();
		await expect(menu.locator('[data-tool="open-details"]')).toBeVisible();
		await expect(menu.locator('[data-tool="edit-image"]')).toBeVisible();
		await expect(menu.locator('[data-tool="download"]')).toBeVisible();
		await expect(menu.locator('[data-tool="delete"]')).toBeVisible();
		await screenshot(page, JOURNEY, 'library-menu-1440');
	});
});

test.describe('per-entry tools menu on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test.beforeEach(async ({ page }) => {
		await loginAsOwner(page);
		await mockMedia(page);
	});

	test('shows a visible menu chip and opens the bottom sheet', async ({ page }) => {
		await page.goto('/history');
		const card = page.locator('[data-generation-card]').first();
		await expect(card).toBeVisible();

		const chip = card.getByRole('button', { name: 'Item actions' });
		await expect(chip).toBeVisible();
		const box = await chip.boundingBox();
		expect(box!.width).toBeGreaterThanOrEqual(31);

		await chip.tap();
		const sheet = page.locator('[data-tools-sheet]');
		await expect(sheet).toBeVisible();
		await expect(sheet.locator('[data-tool="open-details"]')).toBeVisible();
		await expect(sheet.locator('[data-tool="delete"]')).toBeVisible();
		const sheetBox = await sheet.boundingBox();
		expect(sheetBox!.y + sheetBox!.height).toBeGreaterThan(800);
		await screenshot(page, JOURNEY, 'history-sheet-390');
	});
});
