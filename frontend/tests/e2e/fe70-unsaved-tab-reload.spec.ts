import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { installAndSelectImagePreset } from './presetPreamble';

// Regression: a /generate tab whose prompt/settings were configured but
// never saved as a session used to lose everything on a page reload —
// PersistedTab (the localStorage-backed tabsStore snapshot) only carried UI
// chrome (layout, colors, selectedSessionId), never the actual prompt/form
// content, and the auto-restore-on-mount path in +page.svelte only rehydrates
// a tab that HAS a selectedSessionId. This spec configures a named/colored
// prompt section on a fresh (unsaved) tab, reloads the page, and asserts the
// content survived without ever saving a session.

const JOURNEY = 'fe70-unsaved-tab-reload';

async function typeIntoSegment(page: Page, listAriaLabel: string, index: number, text: string) {
	const item = page.locator(`div[role="list"][aria-label="${listAriaLabel}"] [role="listitem"]`).nth(index);
	const editor = item.locator('.inline-chip-editor[role="textbox"]');
	await editor.click();
	await page.keyboard.type(text);
}

async function setSegmentMeta(page: Page, listAriaLabel: string, index: number, name: string, colorName: string) {
	const item = page.locator(`div[role="list"][aria-label="${listAriaLabel}"] [role="listitem"]`).nth(index);
	await item.hover();
	await item.getByRole('button', { name: /^More actions for/ }).click();
	await item.getByRole('menuitem', { name: 'Details' }).click();
	// The segment-composer port renders Details as a real modal
	// (PromptSegmentDetailsModal, portaled onto <body>), not an inline reveal
	// under the card — its fields live in the dialog, not inside the list item,
	// and "Save details" both commits and closes it (Cancel would discard it).
	const dialog = page.getByRole('dialog', { name: 'Segment details' });
	await dialog.getByPlaceholder('Optional segment name').fill(name);
	await dialog.getByRole('button', { name: colorName, exact: true }).click();
	await dialog.getByRole('button', { name: /^Save/ }).click();
	// BaseModal's own exit transition (~150ms) keeps the dialog node — and its
	// focusTrap action — mounted a beat after `isOpen` flips false; that
	// action's `destroy()` restores focus to whatever was focused before the
	// modal opened. Racing that against the very next action (this spec
	// immediately clicks into the segment's editor and types a sentence
	// starting with "A" — the global "open_quick_actions" shortcut) lets the
	// restored focus swallow that keystroke instead of the editor, opening
	// Quick Actions mid-test. Waiting for the dialog to actually disappear
	// absorbs the transition before proceeding.
	await expect(dialog).toBeHidden();
	// Stronger than the dialog's own visibility: BaseModal's full-viewport,
	// z-[9999] backdrop (`aria-label="Close modal"`) is what actually
	// intercepts a later click while it's still attached, even a moment after
	// its own opacity has faded to the point `toBeHidden` would already call
	// it hidden. Wait for it to actually leave the DOM, not just look gone.
	await expect(page.locator('[aria-label="Close modal"].fixed.inset-0')).toHaveCount(0);
}

test('unsaved tab keeps its prompt/segments/negative prompt across a reload', async ({ page }) => {
	await loginAsOwner(page);
	const imagePreset = await installAndSelectImagePreset(page);
	if (!imagePreset) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}
	await page.setViewportSize({ width: 1440, height: 960 });

	const mainList = page.locator('div[role="list"][aria-label="Positive segments"]');
	await expect(mainList).toBeVisible({ timeout: 20000 });

	// Seed a named/colored positive section and a negative prompt — never
	// saved as a session (no "Save session" click anywhere in this spec).
	await setSegmentMeta(page, 'Positive segments', 0, 'SUBJECT', 'Orange');
	await typeIntoSegment(page, 'Positive segments', 0, 'A rain-slicked alley at night, neon reflections on wet asphalt.');

	const negativeList = page.locator('div[role="list"][aria-label="Negative segments"]');
	const negativeWasTyped = await negativeList.isVisible().catch(() => false);
	if (negativeWasTyped) {
		await typeIntoSegment(page, 'Negative segments', 0, 'blurry, low detail, watermark');
	}

	await expect
		.poll(() =>
			page.evaluate(() =>
				Object.keys(localStorage).some((key) => (localStorage.getItem(key) || '').includes('rain-slicked alley'))
			)
		)
		.toBe(true);
	if (negativeWasTyped) {
		await expect
			.poll(() =>
				page.evaluate(() =>
					Object.keys(localStorage).some((key) =>
						(localStorage.getItem(key) || '').includes('blurry, low detail, watermark')
					)
				)
			)
			.toBe(true);
	}
	await screenshot(page, JOURNEY, '00-before-reload');

	const beforeReloadUrl = page.url();

	await page.reload();
	await page.waitForLoadState('networkidle');

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

	await screenshot(page, JOURNEY, '01-after-reload');
});
