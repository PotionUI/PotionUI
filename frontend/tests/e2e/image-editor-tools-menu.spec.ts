import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { fixturePng } from './imageEditorHelpers';

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
	await page.route(/\/api\/generations\/history\/gen-[12](\?.*)?$/, (route) => {
		const id = route.request().url().match(/gen-[12]/)![0];
		return json(route, GENERATIONS.find((entry) => entry.id === id));
	});
	await page.route(/\/api\/library\/items(\?.*)?$/, (route) =>
		json(route, { items: LIBRARY, total: LIBRARY.length, limit: 20, offset: 0 })
	);
	await page.route(/\/api\/tags(\?.*)?$/, (route) => json(route, { tags: [] }));
	await page.route(/\/api\/media\/generations\/gen-[12]\/.+\.png$/, (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PNG })
	);
}

for (const viewport of VIEWPORTS) {
	test.describe(`at ${viewport.name}`, () => {
		test.beforeEach(async ({ page }) => {
			await page.setViewportSize({ width: viewport.width, height: viewport.height });
			await loginAsOwner(page);
			await mockMedia(page);
		});

		test('opens one Image editor tab per image from the History Tools menu', async ({ page }) => {
			await page.goto('/history');
			const selectors = page.getByRole('button', { name: 'Select generation' });
			await expect(selectors).toHaveCount(2);

			await selectors.nth(1).click();
			await page.locator('[data-tools-trigger]').first().click();
			const item = page.locator('[data-tools-menu] [data-tool="edit-image"]');
			await expect(item).toBeVisible();
			await expect(item).toHaveAttribute('aria-disabled', 'false');
			await expect(item).toContainText('Open in Image editor');
			await screenshot(page, JOURNEY, `history-enabled-${viewport.name}`);
			await item.click();

			await expect(page).toHaveURL(/\/generate/);
			await expect(page.getByTestId('tool-tab-host')).toHaveAttribute('data-tool', 'image');
			await expect(page.getByTestId('tool-tab-host').locator('img, canvas').first()).toBeVisible();
		});

		test('opens an Image editor tab from the Library Tools menu', async ({ page }) => {
			await page.goto('/library');
			await page.getByRole('button', { name: 'Select item' }).first().click();
			await page.locator('[data-tools-trigger]').first().click();
			const item = page.locator('[data-tools-menu] [data-tool="edit-image"]');
			await expect(item).toBeVisible();
			await expect(item).toHaveAttribute('aria-disabled', 'false');
			await screenshot(page, JOURNEY, `library-enabled-${viewport.name}`);
			await item.click();

			await expect(page).toHaveURL(/\/generate/);
			await expect(page.getByTestId('tool-tab-host').locator('img, canvas').first()).toBeVisible();
		});
	});
}
