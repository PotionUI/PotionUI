import { test, expect } from '@playwright/test';
import { loginAsOwner, beat } from './helpers';

// Scene 3 (★): /history populated newest-first, apply the "Videos only"
// filter, open one item's detail panel (preset+version, parameters), then
// click "Reuse this generation's settings" - it opens a new /generate tab
// pre-filled from the seeded row (see +page.svelte's handleReuseRequest).
test('history-gallery', async ({ page }) => {
	await loginAsOwner(page);
	await page.goto('/history');
	await page.waitForLoadState('networkidle');
	await beat(page, 1000);

	await page.getByRole('button', { name: 'Videos only' }).click();
	await beat(page, 1200);
	await page.getByRole('button', { name: 'Videos only' }).click();
	await beat(page, 600);

	const menuButton = page
		.locator('[data-generation-card]')
		.first()
		.getByRole('button', { name: 'More actions' });
	await menuButton.scrollIntoViewIfNeeded();
	await menuButton.click({ force: true });
	await page.getByRole('menuitem', { name: 'Open details' }).click();

	await expect(page.getByText('Generation Details')).toBeVisible({ timeout: 15000 });
	await expect(page.getByText('Parameters')).toBeVisible();
	await beat(page, 1500);

	const reuseButton = page.getByTitle("Reuse this generation's settings");
	if (await reuseButton.count()) {
		await reuseButton.click({ force: true });
		await page.waitForURL(/\/generate/, { timeout: 15000 });
		await beat(page, 1500);
	}
});
