import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { expectEditorReady, fixturePng, strokeAcross } from './imageEditorHelpers';

const JOURNEY = 'image-editor-tools-menu';

const VIEWPORTS = [
	{ name: '1440', width: 1440, height: 900 },
	{ name: '390', width: 390, height: 844 }
];

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

function generation(id: string, files: ReturnType<typeof file>[]) {
	return {
		id,
		preset_name: 'Landscape',
		status: 'completed',
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		form_data: {},
		files,
		rating: 0,
		is_favorite: false
	};
}

const GENERATIONS = [
	generation('gen-1', [file(1, 'gen-1/sunset.png')]),
	generation('gen-2', [file(2, 'gen-2/dunes-a.png'), file(3, 'gen-2/dunes-b.png')])
];

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
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) =>
		json(route, { generations: GENERATIONS, total: GENERATIONS.length })
	);
	await page.route(/\/api\/library\/items(\?.*)?$/, (route) =>
		json(route, { items: LIBRARY, total: LIBRARY.length, limit: 20, offset: 0 })
	);
	await page.route(/\/api\/tags(\?.*)?$/, (route) => json(route, { tags: [] }));
	await page.route(/\/api\/media\/generations\/gen-[12]\/.+\.png$/, (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PNG })
	);
}

async function editAndSave(page: Page) {
	const dialog = page.getByRole('dialog', { name: 'Edit image' });
	await expect(dialog).toBeVisible();
	const canvas = dialog.getByLabel('Drawing canvas');
	await expectEditorReady(canvas);
	await strokeAcross(page, canvas, [0.2, 0.5], [0.8, 0.5]);
	await expect(dialog.getByRole('button', { name: /^Undo Brush stroke/ })).toBeEnabled();
	await dialog.getByRole('button', { name: /Save as new/ }).click();
	const popover = page.getByRole('group', { name: 'Save as new upload' });
	await expect(popover).toBeVisible();
	const [saved] = await Promise.all([
		page.waitForResponse((r) => r.url().includes('/api/media/upload') && r.request().method() === 'POST'),
		popover.getByRole('button', { name: 'Save and use' }).click()
	]);
	expect(saved.ok()).toBeTruthy();
	await expect(dialog).toBeHidden({ timeout: 15000 });
	await expect(page.getByText('Saved to your library as a new image')).toBeVisible();
}

for (const viewport of VIEWPORTS) {
	test.describe(`at ${viewport.name}`, () => {
		test.beforeEach(async ({ page }) => {
			await page.setViewportSize({ width: viewport.width, height: viewport.height });
			await loginAsOwner(page);
			await mockMedia(page);
		});

		test('edits a single image from the History Tools menu', async ({ page }) => {
			test.setTimeout(240000);
			await page.goto('/history');
			const selectors = page.getByRole('button', { name: 'Select generation' });
			await expect(selectors).toHaveCount(2);

			await selectors.nth(1).click();
			await page.locator('[data-tools-trigger]').first().click();
			const item = page.locator('[data-tools-menu] [data-tool="edit-image"]');
			await expect(item).toBeVisible();
			await expect(item).toHaveAttribute('aria-disabled', 'true');
			await screenshot(page, JOURNEY, `history-disabled-${viewport.name}`);
			await page.keyboard.press('Escape');
			await selectors.nth(1).click();

			await selectors.nth(0).click();
			await page.locator('[data-tools-trigger]').first().click();
			await expect(item).toHaveAttribute('aria-disabled', 'false');
			await screenshot(page, JOURNEY, `history-enabled-${viewport.name}`);
			await item.click();

			await editAndSave(page);
		});

		test('edits a single image from the Library Tools menu', async ({ page }) => {
			test.setTimeout(240000);
			await page.goto('/library');
			await page.getByRole('button', { name: 'Select item' }).first().click();
			await page.locator('[data-tools-trigger]').first().click();
			const item = page.locator('[data-tools-menu] [data-tool="edit-image"]');
			await expect(item).toBeVisible();
			await expect(item).toHaveAttribute('aria-disabled', 'false');
			await screenshot(page, JOURNEY, `library-enabled-${viewport.name}`);
			await item.click();

			await editAndSave(page);
		});
	});
}
