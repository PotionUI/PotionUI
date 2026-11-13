import { test, expect } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'history-video-details';
const GENERATION_ID = '01SEEDVR2RESTORATION0000001';

function restorationVideo() {
	const now = new Date().toISOString();
	return {
		id: GENERATION_ID,
		preset_id: 'seedvr2/video_upscale',
		preset_name: 'SeedVR2',
		mode: 'video_upscale',
		form_data: {},
		status: 'completed',
		progress: 1,
		created_at: now,
		completed_at: now,
		updated_at: now,
		rating: 0,
		is_favorite: false,
		tags: [],
		files: [
			{
				id: 1,
				file_path: 'e2e/seedvr2-restoration.mp4',
				file_type: 'video',
				is_final: true,
				created_at: now,
				width: 640,
				height: 360,
				duration_seconds: 2,
				fps: 24
			}
		]
	};
}

test('history video opens in the details modal with its preset', async ({ page }) => {
	const item = restorationVideo();
	const requestedMediaTypes: Array<string | null> = [];

	await page.route('**/api/generations/history**', async (route) => {
		const url = new URL(route.request().url());
		const path = url.pathname;
		if (path === '/api/generations/history') {
			requestedMediaTypes.push(url.searchParams.get('media_type'));
			await route.fulfill({ json: { success: true, data: { generations: [item], total: 1 } } });
			return;
		}
		if (path === `/api/generations/history/${GENERATION_ID}`) {
			await route.fulfill({ json: { success: true, data: item } });
			return;
		}
		await route.fallback();
	});

	await loginAsOwner(page);
	await page.goto('/history');
	await page.waitForLoadState('networkidle');

	await page.getByRole('button', { name: 'Videos only' }).click();
	await expect.poll(() => requestedMediaTypes.includes('video'), { timeout: 15000 }).toBe(true);

	const actions = page.locator('[data-generation-card]').getByRole('button', { name: 'Item actions' });
	await expect(actions.first()).toBeVisible({ timeout: 15000 });
	await actions.first().click({ force: true });
	await page.getByRole('menuitem', { name: 'Open details' }).click();

	const dialog = page.getByRole('dialog', { name: 'Generation Details' });
	await expect(dialog).toBeVisible({ timeout: 15000 });
	await expect(dialog.getByRole('heading', { name: 'SeedVR2', exact: true })).toBeVisible({ timeout: 15000 });
	await expect(dialog.locator('[data-details-meta] code')).toHaveText(/^01SEEDVR.+0000001$/, { timeout: 15000 });
	await expect(dialog.getByRole('button', { name: 'Copy ID' })).toBeVisible();
	await expect(page.locator('video').first()).toBeVisible({ timeout: 15000 });
	await screenshot(page, JOURNEY, '01-details');
});
