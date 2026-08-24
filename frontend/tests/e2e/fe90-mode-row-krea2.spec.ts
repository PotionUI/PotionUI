import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot, shotPath } from './helpers';

// Follow-up to the mode-row grid change (54a4592e), which regresses on
// Krea-2 with the krea2-edit plugin enabled: a real preset with a
// plugin-contributed mode (the tooltip-wrapped provenance branch) and
// three segments (txt2img/enhance/edit), which the original fe90-topsection
// journey never exercised (SDXL only has two built-in modes, no plugin).
// This journey enables krea2-edit through the real admin API so the repro is
// the actual bug surface, not a synthetic stand-in.
const JOURNEY = 'fe90-mode-row-krea2';
const BEAT = 400;
const KREA2_PRESET_ID = '4TK1KBQZ2XMB8ME0PTMXS1YJQP';
const PLUGIN_ID = 'krea2-edit';

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

test('mode row — Krea-2 with krea2-edit enabled (plugin-contributed mode)', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const me = await apiGet(page, '/api/auth/me', token);
	const userId = me.data.id as string;

	// Discover + enable the plugin through the real admin API (not a synthetic
	// stand-in) so the tooltip-wrapped sourcePlugin branch is genuinely hit.
	await apiPost(page, '/api/plugins/scan', token);
	const pluginsList = await apiGet(page, '/api/plugins', token);
	const pluginRow = (pluginsList.data || []).find((p: any) => p.id === PLUGIN_ID);
	if (!pluginRow) {
		test.skip(true, `'${PLUGIN_ID}' was not discovered on this throwaway instance.`);
		return;
	}
	if (!pluginRow.enabled) {
		await apiPost(page, `/api/plugins/${PLUGIN_ID}/enable`, token);
	}

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

	// Confirm the plugin's contributed mode is actually present before
	// screenshotting - if it's absent, this run says nothing about the bug.
	const modes = await apiGet(page, `/api/presets/${krea2.id}/modes`, token);
	const modeRows = (modes.data?.modes || []) as Array<{ name: string; label: string; source_plugin?: string | null }>;
	const editMode = modeRows.find((m) => m.source_plugin === PLUGIN_ID);
	expect(editMode, `expected a mode with source_plugin=${PLUGIN_ID}; got ${JSON.stringify(modeRows)}`).toBeTruthy();

	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByText(krea2.name, { exact: true }).first().click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();

	await expect(page.locator('[role="tablist"]').first()).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(BEAT);

	await screenshot(page, JOURNEY, '01-generate-full');

	const presetCard = page.getByRole('button', { name: krea2.name, exact: false }).first();
	await expect(presetCard).toBeVisible();
	const presetHeaderRegion = presetCard.locator('xpath=ancestor::div[contains(@class, "border-b")][1]');
	if ((await presetHeaderRegion.count()) > 0) {
		await presetHeaderRegion.first().screenshot({ path: shotPath(JOURNEY, '02-preset-header') });
	}

	// The mode row specifically - modes.map gives us the exact button labels
	// to find it without hardcoding "Txt2Img/Enhance/Edit" as literal text.
	const modeButtons = page.getByRole('button', { name: modeRows[0]?.label ?? 'Txt2Img' });
	if ((await modeButtons.count()) > 0) {
		const row = modeButtons.first().locator('xpath=ancestor::div[1]');
		await row.screenshot({ path: shotPath(JOURNEY, '03-mode-row') });
	}
});
