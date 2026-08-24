import { test, expect, type Page, type Locator } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { installAndSelectImagePreset } from './presetPreamble';

const JOURNEY = 'session-edit-survives-tab-switch';

// TabBar.svelte mounts a single SessionPill instance for the active tab and
// only changes its `tabId` prop when the user switches generation tabs - it
// does not destroy/recreate the component the way the mobile per-tab layout
// does. A prop change on a live instance re-triggers SessionPill's "sync a
// newly-selected session" reactive block exactly the same way an explicit
// session pick does, so switching away from a tab bound to a saved session
// and back re-fetches that session from the server and overwrites whatever
// the user had typed since the last save - even though tabsStore (the real
// source of truth for the tab's live draft) already held the edit.

async function positiveSegmentEditor(page: Page, index = 0): Promise<Locator> {
	const item = page
		.locator('div[role="list"][aria-label="Positive segments"] [role="listitem"]')
		.nth(index);
	const editor = item.locator('.inline-chip-editor[role="textbox"]');
	await expect(editor).toBeVisible({ timeout: 15000 });
	return editor;
}

async function replaceSegmentText(page: Page, editor: Locator, text: string): Promise<void> {
	await editor.click();
	await page.keyboard.press('ControlOrMeta+a');
	await page.keyboard.press('Backspace');
	await page.keyboard.type(text);
}

test('an unsaved session edit survives switching to another tab and back', async ({ page }) => {
	test.setTimeout(120000);
	await loginAsOwner(page);

	const preset = await installAndSelectImagePreset(page);
	if (!preset) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}

	await page.setViewportSize({ width: 1440, height: 960 });

	const positiveList = page.locator('div[role="list"][aria-label="Positive segments"]');
	await expect(positiveList).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(500);

	// --- Seed and save a session containing the baseline text.
	const editor = await positiveSegmentEditor(page);
	await replaceSegmentText(page, editor, 'saved session baseline');
	await page.waitForTimeout(300);

	await page.locator('button[aria-label="Save as a new session"]').click();
	const saveAsModal = page.locator('.fixed.inset-0');
	await expect(saveAsModal).toBeVisible();
	const sessionName = `fe79-baseline-${Date.now()}`;
	await saveAsModal.getByPlaceholder('Enter session name').fill(sessionName);
	await saveAsModal.getByRole('button', { name: 'Save', exact: true }).click();
	await expect(saveAsModal).toBeHidden({ timeout: 10000 });

	// The pill now reflects a saved, non-dirty session.
	const sessionTrigger = page.locator('button[aria-label="Session"]');
	await expect(sessionTrigger).toContainText(sessionName, { timeout: 10000 });
	await expect(page.locator('button[aria-label="Save session"]')).toBeHidden();
	await screenshot(page, JOURNEY, '00-session-saved');

	// --- Edit the segment without saving again. Give the session fetch,
	// normalization, dirty-signature recompute and the localStorage
	// persistence debounce time to settle before touching tabs, matching the
	// audit's repro steps.
	await replaceSegmentText(page, editor, 'edited draft must survive tab remount');
	await page.waitForTimeout(2000);

	await expect(positiveList).toContainText('edited draft must survive tab remount');
	await expect(page.locator('button[aria-label="Save session"]')).toBeVisible({ timeout: 10000 });
	await screenshot(page, JOURNEY, '01-edited-unsaved');

	// --- Add a second tab, then switch back to the original one.
	await page.locator('button[aria-label="Add new tab"]').click();
	await page.waitForTimeout(1000);

	await page.locator('.book-tab').filter({ hasText: 'Generation 1' }).click();
	await page.waitForTimeout(1500);

	const restoredEditor = await positiveSegmentEditor(page);
	await screenshot(page, JOURNEY, '02-after-tab-switch-back');

	await expect(restoredEditor).toContainText('edited draft must survive tab remount');
	await expect(positiveList).not.toContainText('saved session baseline');
});
