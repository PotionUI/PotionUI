import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken } from './helpers';

async function openAdvancedTab(page: Page) {
	const tab = page.getByRole('tab', { name: 'Advanced', exact: true }).first();
	await expect(tab).toBeVisible({ timeout: 20000 });
	await tab.click();
	await expect(page.locator('[data-field-name="sampler"]')).toBeVisible({ timeout: 20000 });
}

async function openKrea2(page: Page) {
	await page.addInitScript(() => localStorage.setItem('potionui-form-audience', 'advanced'));
	await loginAsOwner(page);
	const token = await ownerToken(page);
	const headers = { Authorization: `Bearer ${token}` };
	const list = await page.request.get('/api/presets?include_uninstalled=true', { headers });
	const presets = ((await list.json()).data || []) as Array<{ id: string; name: string; installed?: boolean }>;
	const preset = presets.find((p) => /krea-?2/i.test(p.name) && !/edit/i.test(p.name));
	test.skip(!preset, 'the Krea-2 preset is not available on this instance');
	if (!preset!.installed) {
		expect((await page.request.post(`/api/presets/${preset!.id}/install`, { headers })).ok()).toBeTruthy();
	}
	const me = await page.request.get('/api/auth/me', { headers });
	const userId = (await me.json()).data.id as string;
	expect(
		(await page.request.post(`/api/presets/${preset!.id}/assign`, { headers, data: { user_ids: [userId] } })).ok()
	).toBeTruthy();

	await page.setViewportSize({ width: 1440, height: 900 });
	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByText(preset!.name, { exact: true }).first().click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
	await openAdvancedTab(page);
}

test('a chosen sampler survives folding and reopening the form', async ({ page }) => {
	await openKrea2(page);

	const trigger = page.locator('[data-field-name="sampler"] button[aria-haspopup="listbox"]');
	await trigger.scrollIntoViewIfNeeded();
	await trigger.click();
	await page.getByRole('option', { name: /Euler SDE/i }).first().click();
	await expect(trigger).toContainText(/Euler SDE/i);

	await page.keyboard.press('q');
	await page.keyboard.press('q');

	if (!(await page.locator('[data-field-name="sampler"]').isVisible())) await openAdvancedTab(page);

	const reopened = page.locator('[data-field-name="sampler"] button[aria-haspopup="listbox"]');
	await expect(reopened).toBeVisible({ timeout: 10000 });
	await expect(reopened).toContainText(/Euler SDE/i);
});
