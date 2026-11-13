import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner } from './helpers';

const PNG_1X1 = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
	'base64'
);

function item(id: string, minutesAgo: number) {
	const created = new Date(Date.now() - minutesAgo * 60_000).toISOString();
	return {
		id,
		preset_id: 'e2e/deep-link',
		preset_name: 'Deep Link',
		mode: 'txt2img',
		form_data: { prompt: id },
		status: 'completed',
		progress: 1,
		created_at: created,
		started_at: created,
		completed_at: created,
		updated_at: created,
		rating: 0,
		is_favorite: false,
		tags: [],
		files: [
			{
				id: Number(id.slice(-1)),
				file_path: `e2e/deep-link-${id}.png`,
				file_type: 'image',
				is_final: true,
				created_at: created,
				width: 512,
				height: 512
			}
		]
	};
}

const FIRST = item('01DEEPLINK000000000000001', 1);
const SECOND = item('01DEEPLINK000000000000002', 2);
const OLD = item('01DEEPLINK000000000000003', 90);
const LISTED = [FIRST, SECOND];
const KNOWN = [FIRST, SECOND, OLD];

async function mockHistory(page: Page) {
	await page.route('**/api/generations/history**', async (route) => {
		const path = new URL(route.request().url()).pathname;
		if (path === '/api/generations/history') {
			await route.fulfill({ json: { success: true, data: { generations: LISTED, total: LISTED.length } } });
			return;
		}
		const found = KNOWN.find((entry) => path === `/api/generations/history/${entry.id}`);
		if (found) {
			await route.fulfill({ json: { success: true, data: found } });
			return;
		}
		if (path.startsWith('/api/generations/history/')) {
			await route.fulfill({ status: 404, json: { success: false, error: 'not_found' } });
			return;
		}
		await route.fallback();
	});
	await page.route('**/*', async (route) => {
		if (route.request().resourceType() === 'image' && /deep-link-/.test(route.request().url())) {
			await route.fulfill({ body: PNG_1X1, contentType: 'image/png' });
			return;
		}
		await route.fallback();
	});
}

const detailsTitle = (page: Page) => page.getByRole('dialog', { name: 'Generation Details' });

test.describe('history deep link', () => {
	test.beforeEach(async ({ page }) => {
		await mockHistory(page);
		await loginAsOwner(page);
	});

	test('a listed generation id opens its details and closing restores History', async ({ page }) => {
		await page.goto(`/history?generation=${FIRST.id}`);
		await expect(detailsTitle(page)).toBeVisible({ timeout: 15000 });
		await expect(page).toHaveURL(new RegExp(`generation=${FIRST.id}`));

		await page.keyboard.press('Escape');
		await expect(detailsTitle(page)).toBeHidden();
		await expect(page).not.toHaveURL(/generation=/);
		await expect(page.locator('[data-generation-card]')).toHaveCount(LISTED.length);
	});

	test('an id outside the loaded page still opens by id', async ({ page }) => {
		await page.goto(`/history?generation=${OLD.id}`);
		await expect(detailsTitle(page)).toBeVisible({ timeout: 15000 });
		await expect(page).toHaveURL(new RegExp(`generation=${OLD.id}`));
	});

	test('stepping to the next generation updates the URL without adding history entries', async ({ page }) => {
		await page.goto(`/history?generation=${FIRST.id}`);
		await expect(detailsTitle(page)).toBeVisible({ timeout: 15000 });
		const entries = await page.evaluate(() => history.length);

		await page.getByRole('button', { name: 'Next generation' }).click();
		await expect(page).toHaveURL(new RegExp(`generation=${SECOND.id}`));
		expect(await page.evaluate(() => history.length)).toBe(entries);
	});

	test('opening a card from the grid puts its id in the URL', async ({ page }) => {
		await page.goto('/history');
		await expect(page.locator('[data-generation-card]')).toHaveCount(LISTED.length, { timeout: 15000 });
		await page
			.locator('[data-generation-card]')
			.first()
			.getByRole('button', { name: 'Item actions' })
			.click({ force: true });
		await page.getByRole('menuitem', { name: 'Open details' }).click();
		await expect(detailsTitle(page)).toBeVisible({ timeout: 15000 });
		await expect(page).toHaveURL(/generation=01DEEPLINK/);
	});

	test('an unknown id shows a not-found state and closing returns to History', async ({ page }) => {
		await page.goto('/history?generation=01DEEPLINKMISSING0000000');
		await expect(page.getByText('Generation not found')).toBeVisible({ timeout: 15000 });
		await expect(detailsTitle(page)).toBeHidden();

		await page.getByRole('button', { name: 'Back to History' }).click();
		await expect(page.getByText('Generation not found')).toBeHidden();
		await expect(page).not.toHaveURL(/generation=/);
		await expect(page.locator('[data-generation-card]')).toHaveCount(LISTED.length);
	});
});
