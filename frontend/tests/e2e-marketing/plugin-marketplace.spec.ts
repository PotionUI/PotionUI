import { test, expect } from '@playwright/test';
import { loginAsOwner, beat } from './helpers';

// Scene 8: /admin -> Plugins, scroll the installed list, toggle one plugin
// on to show the enable/disable control. Seeded plugin catalog (scan-only,
// nothing pre-enabled) comes from tests/e2e/marketing/seed.py.
test('plugin-marketplace', async ({ page }) => {
	await loginAsOwner(page);
	await page.goto('/admin?tab=plugins');
	await page.waitForLoadState('networkidle');
	await beat(page, 1200);

	const list = page.getByRole('main');
	await list.evaluate((el) => el.scrollTo({ top: 200, behavior: 'smooth' }));
	await beat(page, 900);
	await list.evaluate((el) => el.scrollTo({ top: 500, behavior: 'smooth' }));
	await beat(page, 900);

	const enableSwitch = page.getByRole('switch', { name: 'Enable plugin' }).first();
	await expect(enableSwitch).toBeVisible({ timeout: 15000 });
	await enableSwitch.scrollIntoViewIfNeeded();
	await beat(page, 500);
	await enableSwitch.click();
	await beat(page, 1500);
});
