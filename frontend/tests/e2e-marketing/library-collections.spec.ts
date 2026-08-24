import { test, expect } from '@playwright/test';
import { loginAsOwner, beat } from './helpers';

// Scene: /library populated with seeded media organized into two collections
// ("Potion Showcase", "Restorations" - tests/e2e/marketing/seed.py), a tagged
// item, and a quick tag interaction on an item.
test('library-collections', async ({ page }) => {
	await loginAsOwner(page);
	await page.goto('/library');
	await page.waitForLoadState('networkidle');
	await beat(page, 1000);

	const showcase = page.getByText('Potion Showcase', { exact: true });
	await expect(showcase).toBeVisible({ timeout: 15000 });
	await showcase.click();
	await beat(page, 1000);

	const openItem = page.getByRole('button', { name: 'Open library item' }).first();
	await expect(openItem).toBeVisible({ timeout: 15000 });
	await openItem.click();
	await beat(page, 900);

	const tagInput = page.getByPlaceholder('Add tags...');
	if (await tagInput.isVisible().catch(() => false)) {
		await tagInput.scrollIntoViewIfNeeded();
		await tagInput.click();
		await beat(page, 700);
		await tagInput.fill('favorite');
		await beat(page, 700);
		await page.keyboard.press('Enter');
		await beat(page, 900);
	}

	await page.keyboard.press('Escape');
	await beat(page, 500);

	const restorations = page.getByText('Restorations', { exact: true });
	if (await restorations.isVisible().catch(() => false)) {
		await restorations.click();
		await beat(page, 1200);
	}
});
