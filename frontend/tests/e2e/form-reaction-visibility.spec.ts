import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken } from './helpers';

const PRESET_ID = 'field-catalog-all-fields';

async function openCatalog(page: Page) {
	await loginAsOwner(page);
	const token = await ownerToken(page);
	const headers = { Authorization: `Bearer ${token}` };
	const list = await page.request.get('/api/presets?include_uninstalled=true', { headers });
	const presets = ((await list.json()).data || []) as Array<{ id: string; name: string; installed?: boolean }>;
	const preset = presets.find((p) => p.id === PRESET_ID);
	test.skip(!preset, 'the field catalog preset is not available on this instance');
	if (!preset!.installed) {
		expect((await page.request.post(`/api/presets/${PRESET_ID}/install`, { headers })).ok()).toBeTruthy();
	}
	const me = await page.request.get('/api/auth/me', { headers });
	const userId = (await me.json()).data.id as string;
	expect(
		(await page.request.post(`/api/presets/${PRESET_ID}/assign`, { headers, data: { user_ids: [userId] } })).ok()
	).toBeTruthy();

	await page.setViewportSize({ width: 1440, height: 900 });
	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByText(preset!.name, { exact: true }).first().click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
	await expect(page.locator('[data-field-name="reaction_switch"]')).toBeVisible({ timeout: 20000 });
}

async function chooseSwitch(page: Page, label: string) {
	const trigger = page.locator('[data-field-name="reaction_switch"] button[aria-haspopup="listbox"]');
	await trigger.scrollIntoViewIfNeeded();
	await trigger.click();
	await page.getByRole('option', { name: label, exact: true }).first().click();
}

async function clickToggle(page: Page) {
	const toggle = page.locator('[data-field-name="reaction_toggle"] label');
	await toggle.scrollIntoViewIfNeeded();
	await toggle.click();
}

test('a select reaction shows and hides its field on every change', async ({ page }) => {
	await openCatalog(page);
	const revealed = page.locator('[data-field-name="reaction_revealed"]');
	await expect(revealed).toHaveCount(0);

	await chooseSwitch(page, 'Reveal');
	await expect(revealed).toHaveCount(1);

	await chooseSwitch(page, 'Hide');
	await expect(revealed).toHaveCount(0);

	await chooseSwitch(page, 'Reveal');
	await expect(revealed).toHaveCount(1);
});

test('a checkbox reaction shows its field exactly while ticked', async ({ page }) => {
	await openCatalog(page);
	const ticked = page.locator('[data-field-name="reaction_ticked"]');
	const box = page.locator('[data-field-name="reaction_toggle"] input[type="checkbox"]');
	await expect(ticked).toHaveCount(0);

	await clickToggle(page);
	await expect(box).toBeChecked();
	await expect(ticked).toHaveCount(1);

	await clickToggle(page);
	await expect(box).not.toBeChecked();
	await expect(ticked).toHaveCount(0);

	await clickToggle(page);
	await expect(ticked).toHaveCount(1);
});
