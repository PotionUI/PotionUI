import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot, shotPath } from './helpers';

// Visual evidence — the "1a Instrument Pro" top-section restyle: tabs
// row now carries the session pill + "…" overflow on its right (Simple/
// Advanced moved into that overflow menu alongside the view-layout picker),
// and the left settings pane opens with the relocated preset card + mode
// segmented row (PresetSessionBar deleted, its 15 controls re-homed).
const JOURNEY = 'fe90-topsection';
const BEAT = 400;

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

test('top section — tabs row cluster + relocated preset header', async ({ page }) => {
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
	const imagePreset =
		presets.find((p) => /sdxl/i.test(p.name)) ||
		presets.find((p) => p.engine === 'native' && p.category === 'image');

	if (!imagePreset) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}

	if (!imagePreset.installed) {
		await apiPost(page, `/api/presets/${imagePreset.id}/install`, token);
	}
	await apiPost(page, `/api/presets/${imagePreset.id}/assign`, token, { user_ids: [userId] });

	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByText(imagePreset.name, { exact: true }).first().click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();

	await expect(page.locator('[role="tablist"]').first()).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(BEAT);

	// 1. Full page — the decisive comparison against generate_1a.png's top strip.
	await screenshot(page, JOURNEY, '01-generate-full');

	// 2. Tabs row close-up: tab list + session pill + overflow only (slimmer
	// right cluster now that Simple/Advanced lives inside the overflow menu).
	const tabBar = page.locator('.tab-bar-container').first();
	await expect(tabBar).toBeVisible();
	await tabBar.screenshot({ path: shotPath(JOURNEY, '02-tabs-row') });

	// 3. Left-column preset card (mode segmented row directly under it).
	const presetCard = page.getByRole('button', { name: imagePreset.name, exact: false }).first();
	await expect(presetCard).toBeVisible();
	const presetHeaderRegion = presetCard.locator(
		'xpath=ancestor::div[contains(@class, "border-b")][1]'
	);
	if ((await presetHeaderRegion.count()) > 0) {
		await presetHeaderRegion.first().screenshot({ path: shotPath(JOURNEY, '03-preset-header') });
	} else {
		await presetCard.screenshot({ path: shotPath(JOURNEY, '03-preset-header') });
	}

	// 4. Open session panel (SessionPill's dropdown — same SessionControl panel).
	const sessionTrigger = page.getByRole('button', { name: 'Session', exact: true });
	await expect(sessionTrigger).toBeVisible();
	await sessionTrigger.click();
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, '04-session-panel-open');
	await sessionTrigger.click(); // close
	await page.waitForTimeout(BEAT);

	// 5. Open "…" overflow menu - must show BOTH the Simple/Advanced toggle
	// (now a labeled "Field visibility" section) and the view-layout picker.
	const overflowTrigger = page.getByRole('button', { name: 'More view options' });
	await expect(overflowTrigger).toBeVisible();
	await overflowTrigger.click();
	await page.waitForTimeout(BEAT);
	const overflowMenu = page.getByRole('menu', { name: 'More view options' });
	await expect(overflowMenu).toBeVisible();
	await expect(overflowMenu.getByText('Field visibility')).toBeVisible();
	await expect(overflowMenu.getByRole('button', { name: 'Advanced' })).toBeVisible();
	await expect(overflowMenu.getByText('View layout')).toBeVisible();
	await screenshot(page, JOURNEY, '05-overflow-menu-open');

	// 6. Simple/Advanced toggle (inside the menu) still writes the shared
	// audience store - selecting it doesn't close the menu (only a layout
	// pick does), so this stays in the same open state as step 5.
	const advancedButton = overflowMenu.getByRole('button', { name: 'Advanced' });
	await advancedButton.click();
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, '06-advanced-audience');
	await overflowTrigger.click(); // close
	await page.waitForTimeout(BEAT);

	// 7. Mobile: the Studio "preset & session" sheet now mounts PresetHeader +
	// SessionPill in place of the deleted PresetSessionBar / mobile carousel
	// panel - not redesigned, just re-homed, so confirm it still renders both.
	await page.setViewportSize({ width: 390, height: 844 });
	await page.waitForTimeout(BEAT);
	const presetSheetTrigger = page.getByRole('button', { name: 'Open preset and session' });
	await expect(presetSheetTrigger).toBeVisible();
	await presetSheetTrigger.click();
	await page.waitForTimeout(BEAT);
	const presetSheet = page.getByRole('dialog', { name: 'Preset and session' });
	await expect(presetSheet).toBeVisible();
	await expect(presetSheet.getByRole('button', { name: imagePreset.name, exact: false }).first()).toBeVisible();
	await screenshot(page, JOURNEY, '07-mobile-preset-panel');
});
