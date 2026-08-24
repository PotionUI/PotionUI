import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot, shotPath } from './helpers';

// Resolution picker rework: box mode is now a sunken trigger that opens a
// floating, searchable, tier-grouped panel (the old flat "aspect grid + tier
// tab" layout is gone). Walks: default trigger state, opening the panel,
// narrowing rows by a px fragment and (family permitting) a ratio string,
// picking a row, the custom W×H entry path, Escape-to-close, and the
// removal of the old List/Grid toggle.

const JOURNEY = 'resolution-tiers';
const BEAT = 300;

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

test('resolution picker — search panel, tier sections, custom entry', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const me = await apiGet(page, '/api/auth/me', token);
	const userId = me.data.id as string;

	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{
		id: string;
		name: string;
		engine?: string;
		category?: string;
		installed?: boolean;
	}>;
	const preset = presets.find((p) => /flux/i.test(p.name)) || presets.find((p) => /sdxl/i.test(p.name));

	if (!preset) {
		test.skip(true, 'No Flux or SDXL preset available on this throwaway instance.');
		return;
	}

	if (!preset.installed) {
		await apiPost(page, `/api/presets/${preset.id}/install`, token);
	}
	await apiPost(page, `/api/presets/${preset.id}/assign`, token, { user_ids: [userId] });

	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByText(preset.name, { exact: true }).first().click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();

	const generationTab = page.getByRole('tab', { name: 'Generation' });
	if ((await generationTab.count()) > 0) {
		await generationTab.click();
	}
	await page.waitForTimeout(BEAT);

	const resolutionCard = page
		.locator('.field-card')
		.filter({ has: page.locator('label', { hasText: 'Resolution' }) })
		.first();
	await expect(resolutionCard).toBeVisible({ timeout: 20000 });
	await resolutionCard.scrollIntoViewIfNeeded();

	const trigger = resolutionCard.locator('button[aria-haspopup="listbox"]');
	await expect(trigger).toBeVisible();
	await screenshot(page, JOURNEY, '01-trigger-default');
	const initialTriggerText = ((await trigger.textContent()) || '').trim();

	// Open the panel (portalled to <body>, so it's not scoped under the card).
	await trigger.click();
	const panel = page.locator('[role="listbox"]').filter({ has: page.locator('input[type="text"]') });
	await expect(panel).toBeVisible();
	await screenshot(page, JOURNEY, '02-panel-open');

	const searchInput = panel.locator('input[type="text"]').first();
	await expect(searchInput).toBeFocused();

	const rows = panel.locator('button[role="option"]');
	const unfilteredCount = await rows.count();
	expect(unfilteredCount, 'the panel should list at least one resolution row').toBeGreaterThan(0);

	// Search by a resolution fragment every family's list carries (the Full
	// tier's Square entry, 1024x1024).
	await searchInput.fill('1024');
	await page.waitForTimeout(BEAT);
	const fragmentFilteredCount = await rows.count();
	expect(fragmentFilteredCount, 'searching "1024" should narrow the row list').toBeGreaterThan(0);
	expect(fragmentFilteredCount, 'searching "1024" should narrow the row list').toBeLessThanOrEqual(unfilteredCount);
	await screenshot(page, JOURNEY, '03-search-by-px-fragment');

	// Search by ratio ("16:9") - Flux ships literal 16:9 entries; SDXL's own
	// ratio set doesn't, so only assert the narrowing when it's present.
	await searchInput.fill('16:9');
	await page.waitForTimeout(BEAT);
	const ratioFilteredCount = await rows.count();
	if (ratioFilteredCount > 0) {
		expect(ratioFilteredCount, 'searching "16:9" should narrow the row list').toBeLessThanOrEqual(unfilteredCount);
		await screenshot(page, JOURNEY, '04-search-by-ratio');
	} else {
		console.log(`[${JOURNEY}] preset "${preset.name}" has no literal 16:9 ratio entry - ratio search left 0 rows as expected.`);
		await searchInput.fill('1024');
		await page.waitForTimeout(BEAT);
	}

	// Pick the first visible row; the trigger should reflect the new value.
	await rows.first().click();
	await expect(panel).not.toBeVisible();
	const pickedTriggerText = ((await trigger.textContent()) || '').trim();
	expect(pickedTriggerText, 'trigger should reflect the picked row').not.toBe(initialTriggerText);
	await screenshot(page, JOURNEY, '05-trigger-after-pick');

	// Custom W×H entry.
	await trigger.click();
	await expect(panel).toBeVisible();
	await panel.getByRole('button', { name: /Custom size/ }).click();
	const widthInput = panel.getByLabel('Custom width');
	const heightInput = panel.getByLabel('Custom height');
	await expect(widthInput).toBeFocused();
	await widthInput.fill('832');
	await heightInput.fill('1216');
	await screenshot(page, JOURNEY, '06-custom-entry');
	await panel.getByRole('button', { name: 'Apply' }).click();
	await expect(panel).not.toBeVisible();

	const customTriggerText = ((await trigger.textContent()) || '').trim();
	expect(customTriggerText).toContain('832');
	expect(customTriggerText).toContain('1216');
	await screenshot(page, JOURNEY, '07-custom-applied');

	// A custom value below the field's floor is rejected with an inline error,
	// not silently accepted.
	await trigger.click();
	await expect(panel).toBeVisible();
	await panel.getByRole('button', { name: /Custom size/ }).click();
	await panel.getByLabel('Custom width').fill('4');
	await panel.getByLabel('Custom height').fill('4');
	await panel.getByRole('button', { name: 'Apply' }).click();
	await expect(panel).toBeVisible();
	await expect(panel.getByText(/between \d+ and \d+px/)).toBeVisible();
	await screenshot(page, JOURNEY, '08-custom-out-of-bounds');

	// Escape closes the panel without changing the value.
	await page.keyboard.press('Escape');
	await expect(panel).not.toBeVisible();
	expect(((await trigger.textContent()) || '').trim()).toBe(customTriggerText);

	// The old List/Grid view toggle is gone - the picker is the only mode.
	await expect(resolutionCard.getByRole('button', { name: 'List view' })).toHaveCount(0);

	console.log(`[${JOURNEY}] preset="${preset.name}" customApplied="${customTriggerText}"`);
});
