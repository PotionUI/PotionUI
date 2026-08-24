import { test, expect } from '@playwright/test';
import { loginAsOwner, beat } from './helpers';

// Scene 9: /admin -> Backends (native row, real health status) and
// Users/Groups (2 seeded demo accounts + a group with a preset assigned -
// see tests/e2e/marketing/seed.py). No comfyui-engine backend row is seeded
// (see seed.py's docstring for why); only the real `native` backend shows.
test('admin-users-backends', async ({ page }) => {
	await loginAsOwner(page);

	await page.goto('/admin?tab=backends');
	await page.waitForLoadState('networkidle');
	await beat(page, 1000);
	const backendsList = page.getByRole('listbox', { name: 'Backends' });
	await expect(backendsList).toBeVisible({ timeout: 15000 });
	await beat(page, 1500);

	await page.goto('/admin?tab=users');
	await page.waitForLoadState('networkidle');
	await beat(page, 1200);
	await expect(page.getByText('art-lead', { exact: true })).toBeVisible({ timeout: 15000 });
	await beat(page, 900);

	const groupsToggle = page.getByRole('button', { name: /^Groups/ }).first();
	if (await groupsToggle.isVisible().catch(() => false)) {
		await groupsToggle.click();
		await beat(page, 900);
		const teamGroup = page.getByText('Creative Team').first();
		if (await teamGroup.isVisible().catch(() => false)) {
			await teamGroup.click();
			await beat(page, 1200);
		}
	}
	await beat(page, 900);
});
