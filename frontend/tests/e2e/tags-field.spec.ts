import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'tags-field';

async function apiGet(page: Page, url: string, token: string) {
	const res = await page.request.get(url, { headers: { Authorization: `Bearer ${token}` } });
	expect(res.ok(), `GET ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

async function apiPost(page: Page, url: string, token: string, data?: unknown) {
	const res = await page.request.post(url, {
		headers: { Authorization: `Bearer ${token}` },
		data: data ?? {}
	});
	expect(res.ok(), `POST ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

test('tags field: pick, type, remove, and the joined string reaches the form', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const me = await apiGet(page, '/api/auth/me', token);
	const userId = me.data.id as string;

	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{ id: string; name: string; installed?: boolean }>;
	const yue2 = presets.find((p) => p.name === 'YuE2');
	expect(yue2, 'YuE2 preset must be discoverable on this throwaway instance').toBeTruthy();

	if (!yue2!.installed) {
		await apiPost(page, `/api/presets/${yue2!.id}/install`, token);
	}
	await apiPost(page, `/api/presets/${yue2!.id}/assign`, token, { user_ids: [userId] });

	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	const presetList = page.locator('[role="listbox"][aria-label="Presets"]');
	await expect(presetList).toBeVisible({ timeout: 15000 });
	await presetList.getByText(yue2!.name, { exact: true }).click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();

	await expect(page.locator('[role="tablist"]').first()).toBeVisible({ timeout: 20000 });

	const styleField = page.locator('.field-card', { hasText: 'Style tags' });
	await expect(styleField).toBeVisible({ timeout: 20000 });

	const languageSlot = styleField.locator('[data-slot-key="language"]');
	const instrumentalSlot = styleField.locator('[data-slot-key="instrumental"]');
	await expect(languageSlot).toBeVisible();
	await expect(languageSlot).toContainText('Language');
	await expect(languageSlot).toContainText('—');
	await expect(instrumentalSlot).toBeVisible();
	await expect(instrumentalSlot).toContainText('Instrumentation');
	await expect(styleField.getByText('Nothing yet — required before generating', { exact: true })).toBeVisible();

	await screenshot(page, JOURNEY, '01-empty-style-field');

	await instrumentalSlot.getByRole('button', { name: 'Add', exact: true }).click();
	const picker = page.getByRole('dialog', { name: 'Instrumentation picker' });
	await expect(picker).toBeVisible();

	await picker.getByRole('button', { name: 'piano', exact: true }).click();
	await picker.getByRole('button', { name: 'drums', exact: true }).click();

	await expect(instrumentalSlot.getByText('piano', { exact: true })).toBeVisible();
	await expect(instrumentalSlot.getByText('drums', { exact: true })).toBeVisible();
	await expect(picker.getByText('2 / 25', { exact: true })).toBeVisible();

	await screenshot(page, JOURNEY, '02-two-tags-picked');

	await picker.getByPlaceholder('Search instrumentation').fill('glass harmonica solo');
	const addCustomRow = picker.getByRole('button', {
		name: 'Add "glass harmonica solo" as a custom instrumentation tag'
	});
	await expect(addCustomRow).toBeVisible();
	await addCustomRow.click();
	await expect(instrumentalSlot.getByText('glass harmonica solo', { exact: true })).toBeVisible();

	await screenshot(page, JOURNEY, '03-custom-tag-added');

	await instrumentalSlot.locator('button[aria-label="Remove piano"]').click();
	const previewLine = styleField.locator('span.flex-1.font-mono');
	await expect(previewLine).toHaveText('drums, glass harmonica solo');

	await screenshot(page, JOURNEY, '04-after-remove');

	await instrumentalSlot.locator('button[title="Add Instrumentation"]').click();
	await expect(page.getByRole('dialog', { name: 'Instrumentation picker' })).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog', { name: 'Instrumentation picker' })).toHaveCount(0);
	await expect(previewLine).toHaveText('drums, glass harmonica solo');

	await screenshot(page, JOURNEY, '05-after-escape-value-kept');

	console.log(`[${JOURNEY}] style tags field verified through pick/type/remove/escape; joined preview = "drums, glass harmonica solo"`);
});
