import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, screenshot, expectNoBoxOverlap } from './helpers';

const JOURNEY = 'history-generation-time';
const PNG_1X1 = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
	'base64'
);

function file(id: number) {
	return {
		id,
		file_path: `e2e/gen-time-${id}.png`,
		file_type: 'image',
		is_final: true,
		created_at: new Date().toISOString(),
		width: 1024,
		height: 768
	};
}

function item(id: string, minutesAgo: number, over: Record<string, unknown> = {}, fileCount = 1) {
	const created = new Date(Date.now() - minutesAgo * 60_000).toISOString();
	return {
		id,
		preset_id: 'e2e/gen-time',
		preset_name: 'Gen Time',
		mode: 'txt2img',
		form_data: { prompt: id },
		status: 'completed',
		progress: 1,
		created_at: created,
		started_at: created,
		completed_at: created,
		updated_at: created,
		run_seconds: null,
		queued_seconds: null,
		rating: 0,
		is_favorite: false,
		tags: [],
		files: Array.from({ length: fileCount }, (_, i) => file(Number(id.slice(-1)) * 10 + i)),
		...over
	};
}

const BATCH = item('01GENTIME00000000000000A', 1, { run_seconds: 92, queued_seconds: 12 }, 4);
const QUICK = item('01GENTIME00000000000000B', 2, { run_seconds: 8, queued_seconds: 1 });
const UNTIMED = item('01GENTIME00000000000000C', 3);
const RUNNING = item('01GENTIME00000000000000D', 4, {
	status: 'running',
	progress: 0.4,
	completed_at: null,
	run_seconds: null
});
const ITEMS = [BATCH, QUICK, UNTIMED, RUNNING];

async function mockHistory(page: Page) {
	await page.route('**/api/generations/history**', async (route) => {
		const path = new URL(route.request().url()).pathname;
		if (path === '/api/generations/history') {
			await route.fulfill({ json: { success: true, data: { generations: ITEMS, total: ITEMS.length } } });
			return;
		}
		const found = ITEMS.find((entry) => path === `/api/generations/history/${entry.id}`);
		if (found) {
			await route.fulfill({ json: { success: true, data: found } });
			return;
		}
		await route.fallback();
	});
	await page.route('**/*', async (route) => {
		const request = route.request();
		if (request.resourceType() === 'image' && /gen-time-/.test(request.url())) {
			await route.fulfill({ body: PNG_1X1, contentType: 'image/png' });
			return;
		}
		await route.fallback();
	});
}

async function openDetails(page: Page, index: number) {
	const actions = page.locator('[data-generation-card]').getByRole('button', { name: 'Item actions' });
	await actions.nth(index).click({ force: true });
	await page.getByRole('menuitem', { name: 'Open details' }).click();
	await expect(page.getByRole('dialog', { name: 'Generation Details' })).toBeVisible({ timeout: 15000 });
}

for (const viewport of [
	{ width: 1440, height: 900 },
	{ width: 390, height: 844 }
]) {
	test(`history shows generation time on cards and in details at ${viewport.width}`, async ({ page }) => {
		await page.setViewportSize(viewport);
		await mockHistory(page);
		await loginAsOwner(page);
		await page.goto('/history');
		await page.waitForLoadState('networkidle');

		const cards = page.locator('[data-generation-card]');
		await expect(cards).toHaveCount(ITEMS.length, { timeout: 15000 });

		const times = page.getByTestId('card-run-time');
		await expect(times).toHaveCount(2);
		await expect(times.first()).toHaveText('1:32');
		await expect(times.nth(1)).toHaveText('8 s');
		await expect(cards.nth(2).getByTestId('card-run-time')).toHaveCount(0);
		await expect(cards.nth(3).getByTestId('card-run-time')).toHaveCount(0);
		await expect(page.locator('body')).not.toContainText('NaN');
		await screenshot(page, JOURNEY, `01-grid-${viewport.width}`);

		await openDetails(page, 0);
		const row = page.getByTestId('detail-generation-time');
		await expect(row).toHaveText('1 min 32 s · 4 images · queued 12 s');
		await page.locator('[data-stage-meta]').hover();
		const bar = page.locator('[data-details-action-bar]');
		await expect(bar).toBeVisible();
		await expectNoBoxOverlap([
			{ name: 'stage metadata chip', locator: page.locator('[data-stage-meta] > *') },
			{ name: 'action bar button', locator: bar.locator('[data-bar-item]') }
		]);
		const barBox = (await bar.boundingBox())!;
		const stageBox = (await page.locator('[data-details-stage]').boundingBox())!;
		expect(barBox.y + barBox.height).toBeLessThanOrEqual(stageBox.y + 1);
		expect(barBox.x).toBeGreaterThanOrEqual(0);
		expect(barBox.x + barBox.width).toBeLessThanOrEqual(viewport.width + 0.5);
		await screenshot(page, JOURNEY, `02-detail-${viewport.width}`);
		await page.keyboard.press('Escape');

		await openDetails(page, 1);
		await expect(page.getByTestId('detail-generation-time')).toHaveText('8 s');
		await page.keyboard.press('Escape');

		await openDetails(page, 2);
		await expect(page.getByTestId('detail-generation-time')).toHaveCount(0);
		await expect(page.locator('body')).not.toContainText('NaN');
		await screenshot(page, JOURNEY, `03-detail-untimed-${viewport.width}`);
	});
}
