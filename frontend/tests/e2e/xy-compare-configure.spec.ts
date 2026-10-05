import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken } from './helpers';
import { closeCompareDrawer, compareToggle, generateMarkCount, openCompareDrawer, pickAxisField } from './xyCompareHelpers';

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

test('Compare arms next to Continuous, takes sampler by scheduler and locks the axis fields', async ({ page }) => {
	await openKrea2(page);

	const toggle = compareToggle(page);
	await expect(toggle).toBeVisible();
	await expect(toggle).toBeEnabled();
	const drawer = await openCompareDrawer(page);

	await pickAxisField(page, 'X', /^Sampler/);
	await pickAxisField(page, 'Y', /^Schedule/);

	const summary = drawer.getByTestId('compare-summary');
	await expect(summary).toHaveText(/\d+\s*×\s*\d+\s*=\s*\d+ generations/);
	const text = (await summary.textContent()) ?? '';
	const total = Number(/=\s*(\d+)/.exec(text)?.[1]);
	expect(total).toBeGreaterThan(1);

	await expect(generateMarkCount(page)).toHaveText(String(total));
	await expect(compareToggle(page)).toContainText(String(total));
	await expect(page.locator('[data-compare-status]')).toBeVisible();

	await closeCompareDrawer(page);

	const samplerField = page.locator('[data-field-name="sampler"]');
	await expect(samplerField.locator('[data-axis-locked="x"]')).toContainText(/x axis/i);
	await expect(samplerField.locator('[data-axis-locked="x"]')).toContainText(/\d+ values/);
	const scheduleField = page.locator('[data-field-name="schedule"], [data-field-name="scheduler"]').first();
	await expect(scheduleField.locator('[data-axis-locked="y"]')).toContainText(/y axis/i);

	await samplerField.locator('[data-axis-locked="x"]').click();
	await expect(drawer).toBeVisible({ timeout: 10000 });

	await drawer.getByRole('button', { name: 'Turn off' }).click();
	await expect(page.locator('[data-axis-locked]')).toHaveCount(0);
	await expect(toggle).toHaveAttribute('aria-pressed', 'false');
});
