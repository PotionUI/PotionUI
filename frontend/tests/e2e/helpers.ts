import { expect, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

export const OWNER = {
	username: process.env.E2E_USERNAME || 'e2e-owner',
	password: process.env.E2E_PASSWORD || ''
};

const ARTIFACTS_DIR = process.env.E2E_ARTIFACTS_DIR || join(process.cwd(), 'tests', 'e2e', '.playwright-artifacts');

/** Absolute path for a labeled screenshot under artifacts/<journey>/<label>.png. */
export function shotPath(journey: string, label: string): string {
	const dir = join(ARTIFACTS_DIR, journey);
	mkdirSync(dir, { recursive: true });
	return join(dir, `${label}.png`);
}

/** Save a full-page screenshot of the decisive state. Returns its path. */
export async function screenshot(page: Page, journey: string, label: string): Promise<string> {
	const path = shotPath(journey, label);
	await page.screenshot({ path, fullPage: true });
	return path;
}

/** Log in as the throwaway instance owner (ADMIN) through the real login form. */
export async function loginAsOwner(page: Page): Promise<void> {
	await page.goto('/login');
	await page.locator('#username').fill(OWNER.username);
	await page.locator('#password').fill(OWNER.password);
	await page.getByRole('button', { name: 'Sign In' }).click();
	// Successful login redirects to /generate.
	await page.waitForURL(/\/generate/, { timeout: 15000 });
}

/** The instance owner's bearer token, read from localStorage after login. */
export async function ownerToken(page: Page): Promise<string> {
	const token = await page.evaluate(() => localStorage.getItem('auth_token'));
	expect(token, 'expected auth_token in localStorage after login').toBeTruthy();
	return token as string;
}
