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

	const input = page.locator('[data-field-name="sampler"] input');
	await input.scrollIntoViewIfNeeded();
	await input.click();
	await page.getByRole('option', { name: /Euler SDE/i }).first().click();
	await expect(input).toHaveValue(/Euler SDE/i);

	await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
	await page.keyboard.press('q');
	const overlay = page.getByRole('dialog').filter({ has: page.getByRole('button', { name: 'Close floating generation form' }) });
	await expect(overlay).toBeVisible({ timeout: 10000 });
	if (!(await overlay.locator('[data-field-name="sampler"]').isVisible())) {
		await overlay.getByRole('tab', { name: 'Advanced', exact: true }).first().click();
	}
	await expect(overlay.locator('[data-field-name="sampler"] input')).toHaveValue(/Euler SDE/i, { timeout: 10000 });

	await overlay.getByRole('button', { name: 'Close floating generation form' }).first().click();
	await expect(overlay).toHaveCount(0, { timeout: 10000 });

	const docked = page.locator('[data-field-name="sampler"]');
	if (!(await docked.first().isVisible())) await openAdvancedTab(page);
	await expect(docked).toHaveCount(1);
	await expect(docked.locator('input')).toHaveValue(/Euler SDE/i, { timeout: 10000 });
});
