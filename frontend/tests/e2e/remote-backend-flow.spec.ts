import { test, expect } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

// The backend-first remote flow: a native.remote backend is creatable with
// just a name (no URL, no token, no provisioning questions), lands disabled
// with a "Not configured" badge, and its detail pane offers connection
// fields / infrastructure instead of trapping the admin in the create modal.

const JOURNEY = 'remote-backend-flow';

test('a remote worker backend is creatable bare and lands as Not configured', async ({ page }) => {
	await loginAsOwner(page);
	await page.goto('/admin?tab=backends');

	await page.getByRole('button', { name: /Add Backend/i }).click();
	const dialog = page.getByRole('dialog');
	await expect(dialog).toBeVisible();

	// Pick the remote worker driver from the engine select.
	const engineSelect = dialog.locator('select').first();
	await engineSelect.selectOption({ label: 'Native (Remote Worker)' });

	const name = `E2E Remote ${Date.now()}`;
	await dialog.locator('input[type="text"]').first().fill(name);

	// No URL, no token - Create must be enabled and succeed anyway.
	const createButton = dialog.getByRole('button', { name: /Create/i });
	await expect(createButton).toBeEnabled();
	await createButton.click();
	await expect(dialog).toHaveCount(0, { timeout: 10000 });

	// The new backend is in the list and shows the Not configured state.
	await expect(page.getByText(name, { exact: true }).first()).toBeVisible({ timeout: 10000 });
	await expect(page.getByText('Not configured').first()).toBeVisible();

	await screenshot(page, JOURNEY, 'bare-remote-backend-created');

	// No page-level horizontal overflow with the detail pane open.
	const overflowX = await page.evaluate(() => {
		const el = document.scrollingElement!;
		return el.scrollWidth - el.clientWidth;
	});
	expect(overflowX, 'admin page must not scroll horizontally').toBeLessThanOrEqual(0);
});
