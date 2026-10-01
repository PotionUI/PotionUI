import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { installAndSelectImagePreset } from './presetPreamble';

const JOURNEY = 'session-save-keyboard';

async function setup(page: Page): Promise<boolean> {
	await loginAsOwner(page);
	const preset = await installAndSelectImagePreset(page);
	if (!preset) return false;
	await page.setViewportSize({ width: 1440, height: 960 });
	await expect(page.locator('div[role="list"][aria-label="Positive segments"]')).toBeVisible({ timeout: 20000 });
	await page.locator('button[aria-label="Add new tab"]').click();
	await expect(page.locator('.book-tab')).toHaveCount(2, { timeout: 10000 });
	await page.locator('.book-tab').filter({ hasText: 'Generation 1' }).click();
	await expect(page.locator('button[aria-label="Save as a new session"]')).toBeVisible({ timeout: 15000 });
	await page.waitForTimeout(500);
	return true;
}

async function openSaveAs(page: Page) {
	await page.locator('button[aria-label="Save as a new session"]').click();
	const modal = page.locator('.fixed.inset-0');
	await expect(modal).toBeVisible();
	return modal;
}

test('Enter in the session name input saves the session', async ({ page }) => {
	test.setTimeout(120000);
	if (!(await setup(page))) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}
	const modal = await openSaveAs(page);
	const name = `kbd-${Date.now()}`;
	const input = modal.getByPlaceholder('Enter session name');
	await input.fill(name);
	await input.press('Enter');
	await expect(modal).toBeHidden({ timeout: 10000 });
	await expect(page.locator('button[aria-label="Session"]')).toContainText(name, { timeout: 10000 });
	await expect(page.locator('.book-tab')).toHaveCount(2);
	await screenshot(page, JOURNEY, '00-saved-with-enter');
});

test('Enter with focus off the name input never touches the workspace', async ({ page }) => {
	test.setTimeout(120000);
	if (!(await setup(page))) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}
	const modal = await openSaveAs(page);
	const input = modal.getByPlaceholder('Enter session name');
	await input.fill('');
	await modal.getByText('Name', { exact: true }).click();
	await page.keyboard.press('Enter');
	await page.waitForTimeout(800);
	await expect(page.locator('.book-tab')).toHaveCount(2);
	await expect(modal).toBeVisible();
	await screenshot(page, JOURNEY, '01-enter-off-input');

	await input.fill('typed-then-blurred');
	await modal.getByText('Name', { exact: true }).click();
	await page.keyboard.press('Enter');
	await page.waitForTimeout(800);
	await expect(page.locator('.book-tab')).toHaveCount(2);
	await expect(page.locator('button[aria-label="New workspace"]')).toBeVisible();
});

test('Escape closes the save modal without side effects', async ({ page }) => {
	test.setTimeout(120000);
	if (!(await setup(page))) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}
	const modal = await openSaveAs(page);
	await modal.getByPlaceholder('Enter session name').fill('discarded');
	await page.keyboard.press('Escape');
	await expect(modal).toBeHidden({ timeout: 5000 });
	await expect(page.locator('.book-tab')).toHaveCount(2);
	await expect(page.locator('button[aria-label="Save as a new session"]')).toBeVisible();
});
