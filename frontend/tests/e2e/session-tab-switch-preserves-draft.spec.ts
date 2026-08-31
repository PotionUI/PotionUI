import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

// Regression: db97094/b3ad9d3 stopped +page.svelte's own onMount from
// re-applying a saved session over a tab's live draft on SPA remount. But
// switching between the in-app generate tabs never remounts +page.svelte
// itself - it toggles `{#if isActive}` per tab (routes/generate/+page.svelte)
// and re-keys `<GenerationPanel>` on `currentTab.id`, so the active tab's
// whole editor (including SessionCluster, the desktop session bar) is
// destroyed and recreated on every switch. This drives the maintainer's exact
// repro through the real UI: load/save a session, edit a prompt segment
// (making the tab dirty against its saved baseline), switch to a second tab
// and back, and assert the edit survived and the dirty indicator still shows
// it - neither silently reverted to the saved session nor to a stale "clean"
// state.

const JOURNEY = 'session-tab-switch-preserves-draft';

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

async function pickPreset(page: Page, presetName: string) {
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByRole('listbox', { name: 'Presets' }).getByText(presetName, { exact: true }).click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
}

test('editing a segment after loading a session survives switching tabs and back', async ({ page }) => {
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

	await pickPreset(page, imagePreset.name);

	const mainList = page.locator('div[role="list"][aria-label="Positive segments"]');
	await expect(mainList).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(500);

	await typeIntoSegment(page, 'Positive segments', 0, 'A lighthouse on a stormy cliff.');

	// Save it as a session so the tab has a real saved baseline to be dirty against.
	const saveCell = page.getByRole('button', { name: 'Save as a new session' });
	await expect(saveCell).toBeVisible({ timeout: 10000 });
	await saveCell.click();

	const sessionName = `E2E Tab Switch ${Date.now()}`;
	await page.getByPlaceholder('Enter session name').fill(sessionName);
	await page
		.getByRole('dialog')
		.getByRole('button', { name: 'Save', exact: true })
		.click();

	// Session cell now shows the saved name and a "saved" (not dirty) dot.
	await expect(page.getByRole('button', { name: 'Session', exact: true })).toContainText(sessionName, { timeout: 10000 });
	await expect(page.getByRole('button', { name: 'Session saved' })).toBeVisible({ timeout: 10000 });

	// Edit the segment further - now dirty against the saved baseline, never
	// re-saved.
	await typeIntoSegment(page, 'Positive segments', 0, ' Neon light flickers over the waves.');
	await expect(page.getByRole('button', { name: 'Save session' })).toBeVisible({ timeout: 10000 });

	const dirtyText = await mainList.textContent();
	expect(dirtyText).toContain('A lighthouse on a stormy cliff.');
	expect(dirtyText).toContain('Neon light flickers over the waves.');

	await screenshot(page, JOURNEY, '00-dirty-before-tab-switch');

	// Switch to a second tab and back - the exact repro from the bug report.
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await expect(page.getByText('Generation 2', { exact: true })).toBeVisible();

	await page.locator('button.book-tab', { hasText: 'Generation 1' }).click();

	const restoredList = page.locator('div[role="list"][aria-label="Positive segments"]');
	await expect(restoredList).toBeVisible({ timeout: 20000 });

	const restoredText = await restoredList.textContent();
	expect(restoredText, 'the dirty edit must survive the tab round trip, not revert to the saved session').toContain(
		'A lighthouse on a stormy cliff.'
	);
	expect(restoredText, 'the dirty edit must survive the tab round trip, not revert to the saved session').toContain(
		'Neon light flickers over the waves.'
	);

	// The dirty indicator must still read dirty - not silently "Saved" as if
	// the round trip re-synced against the server's (stale) saved copy.
	await expect(page.getByRole('button', { name: 'Save session' })).toBeVisible({ timeout: 10000 });

	await screenshot(page, JOURNEY, '01-after-tab-switch-round-trip');

	console.log(`[${JOURNEY}] segment edit + dirty indicator survived a tab switch round trip`);
});

// The maintainer's actual repro loads an EXISTING session through the picker
// (SessionCluster's handleSessionSelect -> applySessionModeData), not a fresh
// save-as-new (confirmSaveSession). The two write different tab fields -
// applySessionModeData sets `sessionBaselineAwaitingFormNormalization: true`
// (DynamicForm's schema-default merge is expected to consume it once and
// normalize the saved baseline), while save-as-new sets it to `false`
// immediately via recordSavedBaseline's default param. Drives session
// creation on tab 1, then the real picker load on a second tab, then the
// same edit + tab-switch round trip as the test above.
test('loading an existing session on a second tab, then editing it, survives switching tabs and back', async ({ page }) => {
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

	// Tab 1: create and save a session to load later.
	await pickPreset(page, imagePreset.name);

	const mainList = page.locator('div[role="list"][aria-label="Positive segments"]');
	await expect(mainList).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(500);

	await typeIntoSegment(page, 'Positive segments', 0, 'A quiet harbor at dawn.');

	const saveCell = page.getByRole('button', { name: 'Save as a new session' });
	await expect(saveCell).toBeVisible({ timeout: 10000 });
	await saveCell.click();

	const sessionName = `E2E Load Round Trip ${Date.now()}`;
	await page.getByPlaceholder('Enter session name').fill(sessionName);
	await page
		.getByRole('dialog')
		.getByRole('button', { name: 'Save', exact: true })
		.click();
	await expect(page.getByRole('button', { name: 'Session', exact: true })).toContainText(sessionName, { timeout: 10000 });

	// Tab 2: a fresh tab, same preset, load the session through the picker
	// (not save-as-new).
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await expect(page.getByText('Generation 2', { exact: true })).toBeVisible();

	await pickPreset(page, imagePreset.name);
	await expect(mainList).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(500);

	await page.getByRole('button', { name: 'Session', exact: true }).click();
	await page.getByRole('menuitem', { name: new RegExp(sessionName) }).click();

	// Confirm the load actually applied before touching anything.
	await expect(mainList).toContainText('A quiet harbor at dawn.', { timeout: 10000 });
	await expect(page.getByRole('button', { name: 'Session', exact: true })).toContainText(sessionName, { timeout: 10000 });
	await expect(page.getByRole('button', { name: 'Session saved' })).toBeVisible({ timeout: 10000 });

	// Edit after the load - dirty against the just-loaded baseline, never
	// re-saved.
	await typeIntoSegment(page, 'Positive segments', 0, ' Gulls circle the empty pier.');
	await expect(page.getByRole('button', { name: 'Save session' })).toBeVisible({ timeout: 10000 });

	const dirtyText = await mainList.textContent();
	expect(dirtyText).toContain('A quiet harbor at dawn.');
	expect(dirtyText).toContain('Gulls circle the empty pier.');

	await screenshot(page, JOURNEY, '02-loaded-and-dirty-before-tab-switch');

	// Switch to tab 1 and back to tab 2 - the exact repro from the bug report,
	// with a LOADED (not saved-on-this-tab) session.
	await page.locator('button.book-tab', { hasText: 'Generation 1' }).click();
	await page.locator('button.book-tab', { hasText: 'Generation 2' }).click();

	const restoredList = page.locator('div[role="list"][aria-label="Positive segments"]');
	await expect(restoredList).toBeVisible({ timeout: 20000 });

	const restoredText = await restoredList.textContent();
	expect(restoredText, 'the post-load edit must survive the tab round trip, not revert to the loaded session').toContain(
		'A quiet harbor at dawn.'
	);
	expect(restoredText, 'the post-load edit must survive the tab round trip, not revert to the loaded session').toContain(
		'Gulls circle the empty pier.'
	);

	await expect(page.getByRole('button', { name: 'Save session' })).toBeVisible({ timeout: 10000 });

	await screenshot(page, JOURNEY, '03-after-load-tab-switch-round-trip');

	console.log(`[${JOURNEY}] loaded-session edit + dirty indicator survived a tab switch round trip`);
});
