import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot, shotPath } from './helpers';

// Visual + functional capture: the negative editor goes visibly inert
// when the resolved guidance can't reach the model. Z-Image's default "turbo"
// speed profile resolves cfg -> 1.0 (single forward, no CFG), so the negative
// footer must show "Not applied at current settings"; switching to "Base"
// resolves cfg -> 4.0 (true CFG) and the notice must clear. No generation runs
// (no GPU) — this drives only the form's resolved-guidance reactivity.

const JOURNEY = 'cmb38-negative-inert';
// Wording changed from "Not applied at current settings" to "Not applied at
// current guidance" in bbff1e9f ("The prompt text becomes the interface in
// the segment editor"), which also removed the negative region's collapse
// toggle — the negative section is always expanded now.
const INERT_NOTICE = 'Not applied at current guidance';

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

test('negative editor marks itself inert at guidance <= 1', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const me = await apiGet(page, '/api/auth/me', token);
	const userId = me.data.id as string;

	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{ id: string; name: string; installed?: boolean }>;
	const zimage = presets.find((p) => /z-?image/i.test(p.id) || /z-?image/i.test(p.name));

	console.log(`[${JOURNEY}] presets=${presets.length} zimage=${zimage?.id}`);
	if (!zimage) {
		test.skip(true, 'No Z-Image preset available on this throwaway instance.');
		return;
	}

	if (!zimage.installed) await apiPost(page, `/api/presets/${zimage.id}/install`, token);
	await apiPost(page, `/api/presets/${zimage.id}/assign`, token, { user_ids: [userId] });

	await page.setViewportSize({ width: 1440, height: 960 });
	await page.goto('/generate');
	await page.waitForLoadState('networkidle');
	await page.waitForTimeout(1000);

	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByRole('listbox', { name: 'Presets' }).getByText(zimage.name, { exact: true }).click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();

	const mainList = page.locator('div[role="list"][aria-label="Positive segments"]');
	await expect(mainList).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(800);

	// Default profile is turbo -> cfg resolves to 1.0 -> the negative is never
	// encoded, so the footer carries the inert notice.
	const notice = page.getByText(INERT_NOTICE, { exact: true });
	await expect(notice).toBeVisible({ timeout: 10000 });
	await notice.scrollIntoViewIfNeeded();
	await page.waitForTimeout(300);
	// Element-scoped capture of the prompt panel so the negative footer + notice
	// are actually framed (the panel lives in a below-the-fold scroll container).
	// `.prompt-panel` was renamed `.prompt-editor` in bbff1e9f.
	const promptPanel = page.locator('.prompt-editor').first();
	await promptPanel.screenshot({ path: shotPath(JOURNEY, '00-zimage-turbo-negative-inert') });
	await screenshot(page, JOURNEY, '00-zimage-turbo-fullpage');

	// Switch Speed: turbo -> Base. The cfg reaction resolves to 4.0 (true CFG),
	// so the negative IS encoded and the notice must disappear.
	const speedTrigger = page
		.getByRole('button')
		.filter({ hasText: /Turbo \(8 steps/ })
		.first();
	await speedTrigger.click();
	await page.getByRole('option', { name: /Base \(30 steps\)/ }).click();
	await page.waitForTimeout(600);

	await expect(page.getByText(INERT_NOTICE, { exact: true })).toHaveCount(0);
	// The negative region is no longer collapsible (bbff1e9f) — scroll its
	// always-visible section header into view instead of a toggle button.
	const negativeHeader = page.locator('.negative-header').first();
	await negativeHeader.scrollIntoViewIfNeeded();
	await page.waitForTimeout(300);
	await promptPanel.screenshot({ path: shotPath(JOURNEY, '01-zimage-base-negative-applied') });
});
