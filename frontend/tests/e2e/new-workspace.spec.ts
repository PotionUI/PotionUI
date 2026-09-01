import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

// "New workspace" wipes every open tab down to one fresh empty tab.
// When the workspace has unsaved changes it must ask first (3-way modal:
// Save & create new / Discard & create new / Cancel) rather than silently
// discarding a draft — same "dirty draft is authoritative" ruling as
// session-tab-switch-preserves-draft.spec.ts.

const JOURNEY = 'new-workspace';

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

test('New workspace warns and discards on a dirty, never-saved tab', async ({ page }) => {
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

	// Clicking "New workspace" on a pristine app (single default tab, nothing
	// picked or typed) must wipe with no modal at all.
	const newWorkspaceButton = page.getByRole('button', { name: 'New workspace' });
	await expect(newWorkspaceButton).toBeVisible({ timeout: 10000 });
	await newWorkspaceButton.click();
	await expect(page.getByRole('heading', { name: 'Unsaved changes' })).not.toBeVisible();
	await expect(page.locator('button.book-tab')).toHaveCount(1);

	// Make a real, unsaved change: pick a preset and type into the prompt -
	// never saved as a session.
	await pickPreset(page, imagePreset.name);

	const mainList = page.locator('div[role="list"][aria-label="Positive segments"]');
	await expect(mainList).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(500);

	await typeIntoSegment(page, 'Positive segments', 0, 'A lighthouse on a stormy cliff.');
	await expect(mainList).toContainText('A lighthouse on a stormy cliff.');

	await screenshot(page, JOURNEY, '00-dirty-draft-before-new-workspace');

	// Click New workspace - since the draft was never saved, the 3-way modal
	// must appear instead of silently wiping it.
	await newWorkspaceButton.click();

	const modalTitle = page.getByRole('heading', { name: 'Unsaved changes' });
	await expect(modalTitle).toBeVisible({ timeout: 10000 });
	await expect(page.getByRole('button', { name: 'Save & create new' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Discard & create new' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible();

	await screenshot(page, JOURNEY, '01-unsaved-changes-modal');

	// Cancel must leave everything untouched.
	await page.getByRole('button', { name: 'Cancel' }).click();
	await expect(modalTitle).not.toBeVisible();
	await expect(mainList).toContainText('A lighthouse on a stormy cliff.');

	// Re-open and discard - the draft is thrown away and exactly one fresh,
	// empty tab is left, focused.
	await newWorkspaceButton.click();
	await expect(modalTitle).toBeVisible({ timeout: 10000 });
	await page.getByRole('button', { name: 'Discard & create new' }).click();
	await expect(modalTitle).not.toBeVisible();

	await expect(page.locator('button.book-tab')).toHaveCount(1);
	await expect(page.getByText('Generation 1', { exact: true })).toBeVisible();

	// The fresh tab must be genuinely empty - no preset carried over.
	await expect(page.getByRole('button', { name: 'Choose a preset' })).toBeVisible({ timeout: 10000 });

	await screenshot(page, JOURNEY, '02-after-discard-single-empty-tab');

	console.log(`[${JOURNEY}] New workspace warned on a dirty draft and discarded it cleanly`);
});

test('New workspace "Save & create new" saves a dirty existing session before wiping', async ({ page }) => {
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

	await typeIntoSegment(page, 'Positive segments', 0, 'A quiet harbor at dawn.');

	const saveCell = page.getByRole('button', { name: 'Save as a new session' });
	await expect(saveCell).toBeVisible({ timeout: 10000 });
	await saveCell.click();

	const sessionName = `E2E New Workspace Save ${Date.now()}`;
	await page.getByPlaceholder('Enter session name').fill(sessionName);
	await page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click();
	await expect(page.getByRole('button', { name: 'Session saved' })).toBeVisible({ timeout: 10000 });

	// Edit further - dirty against the just-saved baseline, never re-saved.
	await typeIntoSegment(page, 'Positive segments', 0, ' Gulls circle the empty pier.');
	await expect(page.getByRole('button', { name: 'Save session' })).toBeVisible({ timeout: 10000 });

	const newWorkspaceButton = page.getByRole('button', { name: 'New workspace' });
	await newWorkspaceButton.click();

	const modalTitle = page.getByRole('heading', { name: 'Unsaved changes' });
	await expect(modalTitle).toBeVisible({ timeout: 10000 });

	await screenshot(page, JOURNEY, '03-save-and-create-new-modal');

	await page.getByRole('button', { name: 'Save & create new' }).click();
	await expect(modalTitle).not.toBeVisible({ timeout: 10000 });

	// The wipe left exactly one fresh, empty tab.
	await expect(page.locator('button.book-tab')).toHaveCount(1);
	await expect(page.getByRole('button', { name: 'Choose a preset' })).toBeVisible({ timeout: 10000 });

	// The session itself now carries the edit that was made right before the
	// save, proving the real save flow ran (not a silent discard).
	const sessions = await apiGet(page, `/api/sessions/preset/${imagePreset.id}`, token);
	const saved = (sessions.data || []).find((s: { name: string }) => s.name === sessionName);
	expect(saved, 'expected the session created in this test to still exist').toBeTruthy();
	expect(
		JSON.stringify(saved.data),
		'expected the post-save edit to have been persisted by "Save & create new", not discarded'
	).toContain('Gulls circle the empty pier.');

	await screenshot(page, JOURNEY, '04-after-save-and-create-new-single-empty-tab');

	console.log(`[${JOURNEY}] "Save & create new" saved the dirty session before wiping tabs`);
});
