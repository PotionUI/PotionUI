import { test, expect } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot, shotPath, apiGet, apiPost } from './helpers';
import { presetOption } from './presetPreamble';

const JOURNEY = 'fe90-mode-row-krea2';
const KREA2_PRESET_ID = '4TK1KBQZ2XMB8ME0PTMXS1YJQP';

test('mode row — Krea-2 with three segments (txt2img/enhance/edit)', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const me = await apiGet(page, '/api/auth/me', token);
	const userId = me.data.id as string;

	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{ id: string; name: string; installed?: boolean }>;
	const krea2 = presets.find((p) => p.id === KREA2_PRESET_ID);
	if (!krea2) {
		test.skip(true, 'Krea-2 preset not found on this throwaway instance.');
		return;
	}
	if (!krea2.installed) {
		await apiPost(page, `/api/presets/${krea2.id}/install`, token);
	}
	await apiPost(page, `/api/presets/${krea2.id}/assign`, token, { user_ids: [userId] });

	const modes = await apiGet(page, `/api/presets/${krea2.id}/modes`, token);
	const modeRows = (modes.data?.modes || []) as Array<{ name: string; label: string; source_plugin?: string | null }>;
	const editMode = modeRows.find((m) => m.name === 'edit');
	expect(editMode, `expected an edit mode; got ${JSON.stringify(modeRows)}`).toBeTruthy();
	expect(editMode!.source_plugin ?? null).toBeNull();

	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await presetOption(page, krea2.name).click();
	await expect(page.getByRole('dialog', { name: 'Choose preset' })).toBeHidden();

	await expect(page.locator('[role="tablist"]').first()).toBeVisible({ timeout: 20000 });

	await screenshot(page, JOURNEY, '01-generate-full');

	const presetCard = page.getByRole('button', { name: krea2.name, exact: false }).first();
	await expect(presetCard).toBeVisible();
	const presetHeaderRegion = presetCard.locator('xpath=ancestor::div[contains(@class, "border-b")][1]');
	if ((await presetHeaderRegion.count()) > 0) {
		await presetHeaderRegion.first().screenshot({ path: shotPath(JOURNEY, '02-preset-header') });
	}

	const modeSelect = page.getByTestId('preset-header-mode');
	await expect(modeSelect).toBeVisible();
	await modeSelect.getByRole('button').first().click();
	await expect(page.getByRole('option', { name: new RegExp(editMode!.label) })).toBeVisible();
	await page.getByTestId('context-bar').screenshot({ path: shotPath(JOURNEY, '03-mode-row') });
});
