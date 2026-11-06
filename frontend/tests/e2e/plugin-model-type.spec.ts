import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'plugin-model-type';
const LABEL = 'Fixture rig';
const FILENAME_STEM = 'fixture-rig';

function visibleText(page: Page, text: string) {
	return page.getByText(text).filter({ visible: true }).first();
}

test.describe('a model type registered by a plugin', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('shows in Models with its label, its file listed under it', async ({ page }) => {
		await loginAsOwner(page);
		await page.goto('/admin?tab=models');
		await expect(page.getByText('By type')).toBeVisible({ timeout: 15000 });

		const row = page.getByRole('option', { name: new RegExp(LABEL) });
		await expect(row).toBeVisible({ timeout: 15000 });
		await expect(row).toContainText('1');
		await expect(page.getByRole('option', { name: /E2e Rig/ })).toHaveCount(0);
		await screenshot(page, JOURNEY, 'desktop-type-row');

		await row.click();
		await expect(visibleText(page, FILENAME_STEM)).toBeVisible({ timeout: 15000 });
		await screenshot(page, JOURNEY, 'desktop-type-list');

		await visibleText(page, FILENAME_STEM).click();
		const select = page.locator('#model-type');
		await expect(select).toBeVisible({ timeout: 15000 });
		await expect(select).toHaveValue('e2e_rig');
		await expect(select.locator('option:checked')).toHaveText(LABEL);
		await screenshot(page, JOURNEY, 'desktop-detail');
	});
});
