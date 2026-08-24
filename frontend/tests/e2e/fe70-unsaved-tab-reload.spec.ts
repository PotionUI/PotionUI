import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

// Regression: a /generate tab whose prompt/settings were configured but
// never saved as a session used to lose everything on a page reload —
// PersistedTab (the localStorage-backed tabsStore snapshot) only carried UI
// chrome (layout, colors, selectedSessionId), never the actual prompt/form
// content, and the auto-restore-on-mount path in +page.svelte only rehydrates
// a tab that HAS a selectedSessionId. This spec configures a named/colored
// prompt section on a fresh (unsaved) tab, reloads the page, and asserts the
// content survived without ever saving a session.

const JOURNEY = 'fe70-unsaved-tab-reload';

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

async function typeIntoSegment(page: Page, listAriaLabel: string, index: number, text: string) {
	const item = page.locator(`div[role="list"][aria-label="${listAriaLabel}"] [role="listitem"]`).nth(index);
	const editor = item.locator('.inline-chip-editor[role="textbox"]');
	await editor.click();
	await page.keyboard.type(text);
}

async function setSegmentMeta(page: Page, listAriaLabel: string, index: number, name: string, colorName: string) {
	// Content cards surface "Details" as a footer button directly rather than
	// through the "…" popup menu (the menu drops it — and Duplicate/Disable —
	// once a footer already offers them, see PromptSegmentActionMenu's
	// footerActionsShown gate). Colour is one of the PRESET_COLORS swatches,
	// not a free-text field.
	const item = page.locator(`div[role="list"][aria-label="${listAriaLabel}"] [role="listitem"]`).nth(index);
	await item.hover();
	await item.getByRole('button', { name: 'Details' }).click();
	await item.getByPlaceholder('Optional segment name').fill(name);
	await item.getByRole('button', { name: colorName, exact: true }).click();
	await item.getByRole('button', { name: 'Details' }).click();
}

test('unsaved tab keeps its prompt/segments/negative prompt across a reload', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const me = await apiGet(page, '/api/auth/me', token);
	const userId = me.data.id as string;

	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{ id: string; name: string; engine?: string; category?: string; installed?: boolean }>;
	const imagePreset =
		presets.find((p) => /sdxl/i.test(p.id)) ||
		presets.find((p) => p.engine === 'native' && p.category === 'image');

	if (!imagePreset) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}

	if (!imagePreset.installed) {
		await apiPost(page, `/api/presets/${imagePreset.id}/install`, token);
	}
	await apiPost(page, `/api/presets/${imagePreset.id}/assign`, token, { user_ids: [userId] });

	await page.setViewportSize({ width: 1440, height: 960 });
	await page.goto('/generate');
	await page.waitForLoadState('networkidle');
	await page.waitForTimeout(1000);

	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByRole('listbox', { name: 'Presets' }).getByText(imagePreset.name, { exact: true }).click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();

	const mainList = page.locator('div[role="list"][aria-label="Positive segments"]');
	await expect(mainList).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(500);

	// Seed a named/colored positive section and a negative prompt — never
	// saved as a session (no "Save session" click anywhere in this spec).
	await setSegmentMeta(page, 'Positive segments', 0, 'SUBJECT', 'Orange');
	await typeIntoSegment(page, 'Positive segments', 0, 'A rain-slicked alley at night, neon reflections on wet asphalt.');

	const negativeList = page.locator('div[role="list"][aria-label="Negative segments"]');
	if (await negativeList.isVisible().catch(() => false)) {
		await typeIntoSegment(page, 'Negative segments', 0, 'blurry, low detail, watermark');
	}

	// Give the 500ms-debounced localStorage save time to fire.
	await page.waitForTimeout(1000);
	await screenshot(page, JOURNEY, '00-before-reload');

	const beforeReloadUrl = page.url();

	await page.reload();
	await page.waitForLoadState('networkidle');
	await page.waitForTimeout(1500);

	expect(page.url()).toBe(beforeReloadUrl);

	const restoredList = page.locator('div[role="list"][aria-label="Positive segments"]');
	await expect(restoredList).toBeVisible({ timeout: 20000 });
	await expect(restoredList).toContainText('A rain-slicked alley at night, neon reflections on wet asphalt.');
	// `.section-rule` was replaced by `.card-name` on the card head (bbff1e9f) —
	// the segment's own name button, not the plain `.index` ordinal.
	await expect(restoredList.locator('.card-name').first()).toContainText('SUBJECT');

	const restoredNegativeList = page.locator('div[role="list"][aria-label="Negative segments"]');
	if (await restoredNegativeList.isVisible().catch(() => false)) {
		await expect(restoredNegativeList).toContainText('blurry, low detail, watermark');
	}

	await page.waitForTimeout(300);
	await screenshot(page, JOURNEY, '01-after-reload');
});
