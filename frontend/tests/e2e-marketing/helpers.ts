import { expect, type Page } from '@playwright/test';

// Same owner-login pattern as frontend/tests/e2e/helpers.ts, duplicated
// rather than imported across testDir boundaries (this config's testDir is
// this directory, not ../e2e - see playwright.marketing.config.ts).
export const OWNER = {
	username: process.env.E2E_USERNAME || 'e2e-owner',
	password: process.env.E2E_PASSWORD || ''
};

export async function loginAsOwner(page: Page): Promise<void> {
	await page.goto('/login');
	await page.locator('#username').fill(OWNER.username);
	await page.locator('#password').fill(OWNER.password);
	await page.getByRole('button', { name: 'Sign In' }).click();
	await page.waitForURL(/\/generate/, { timeout: 15000 });
}

/** The instance owner's bearer token, read from localStorage after login. */
export async function ownerToken(page: Page): Promise<string> {
	const token = await page.evaluate(() => localStorage.getItem('auth_token'));
	if (!token) throw new Error('expected auth_token in localStorage after login');
	return token;
}

/** Open a preset by (exact) display name from the /generate picker. */
export async function choosePreset(page: Page, presetName: string): Promise<void> {
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByRole('listbox', { name: 'Presets' }).getByText(presetName, { exact: true }).click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
}

/** A short, named pause between interactions so the recorded scene reads at
 * a human pace instead of an instant snap between states. */
export async function beat(page: Page, ms = 500): Promise<void> {
	await page.waitForTimeout(ms);
}
