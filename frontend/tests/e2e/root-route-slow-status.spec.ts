import { test, expect } from '@playwright/test';

test.use({ storageState: { cookies: [], origins: [] } });

test('an unclaimed instance reaches the claim screen from / however slowly the setup status answers', async ({ page }) => {
	await page.route('**/api/setup/status', async (route) => {
		await new Promise((resolve) => setTimeout(resolve, 400));
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ needs_owner: true, registration_open: true, claim_requires_token: false })
		});
	});
	await page.goto('/');
	await expect(page).toHaveURL(/\/setup\/claim$/, { timeout: 10000 });
});
