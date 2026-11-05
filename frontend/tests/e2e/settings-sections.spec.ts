import { test, expect, type Page } from '@playwright/test';
import { fulfillJson, loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'settings-sections';

const SECTIONS = [
	{ id: 'account', label: 'Account', marker: '[aria-label="Change avatar"]' },
	{ id: 'plan', label: 'Plan & usage', marker: '#plan' },
	{ id: 'appearance', label: 'Appearance', marker: '[role="radiogroup"][aria-label="Theme"]' },
	{ id: 'content', label: 'Content', marker: '[role="radiogroup"][aria-label="Sensitive media"]' },
	{ id: 'notifications', label: 'Notifications', marker: '#notifications' },
	{ id: 'mcp', label: 'MCP', marker: 'text=Connect an external AI client' }
] as const;

const NOT_READY = {
	overall: 'not_ready',
	checks: [{ area: 'content', status: 'not_ready', code: 'no_presets', message: 'No presets are installed.', action: null }]
};

const PREFS = {
	sound: false,
	chat_sound: false,
	types: [{ key: 'generation_complete', label: 'Generation finished', description: '', category: 'Generation', enabled: true }]
};

async function mockPrefs(page: Page) {
	await page.route('**/api/notifications/types*', (route) =>
		route.request().method() === 'GET' ? fulfillJson(route, { success: true, data: PREFS }) : route.fallback()
	);
}

async function heading(page: Page): Promise<string> {
	return ((await page.locator('h1').first().textContent()) ?? '').trim();
}

async function expectNoHorizontalOverflow(page: Page) {
	const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
	expect(overflow, 'page scrolls horizontally').toBeLessThanOrEqual(0);
}

async function expectSection(page: Page, id: (typeof SECTIONS)[number]['id']) {
	const entry = SECTIONS.find((candidate) => candidate.id === id)!;
	await expect.poll(() => heading(page), { timeout: 15000 }).toBe(entry.label);
	await expect(page.locator(entry.marker).first()).toBeVisible({ timeout: 15000 });
}

test.describe('user settings sections on desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('every section is reachable from the sidebar and shows its own cards', async ({ page }) => {
		test.setTimeout(90000);
		await mockPrefs(page);
		await loginAsOwner(page);
		await page.goto('/settings');
		const sidebar = page.getByRole('listbox', { name: 'Settings sections' });
		await expect(sidebar).toBeVisible({ timeout: 15000 });
		await expect(sidebar.getByRole('option')).toHaveText(SECTIONS.map((entry) => entry.label));
		await expectSection(page, 'account');

		for (const entry of SECTIONS) {
			await sidebar.getByRole('option', { name: entry.label, exact: true }).click();
			await expectSection(page, entry.id);
			if (entry.id !== 'account') await expect(page).toHaveURL(new RegExp(`/settings\\?section=${entry.id}$`));
			await expectNoHorizontalOverflow(page);
			await screenshot(page, JOURNEY, `${entry.id}-1440`);
		}
	});

	test('the section switch replaces the history entry', async ({ page }) => {
		test.setTimeout(60000);
		await mockPrefs(page);
		await loginAsOwner(page);
		await page.goto('/settings');
		const sidebar = page.getByRole('listbox', { name: 'Settings sections' });
		await sidebar.getByRole('option', { name: 'Appearance', exact: true }).click();
		await expectSection(page, 'appearance');
		await page.goBack();
		await expect(page).not.toHaveURL(/\/settings/);
	});

	test('?section=mcp opens the MCP section', async ({ page }) => {
		test.setTimeout(60000);
		await mockPrefs(page);
		await loginAsOwner(page);
		await page.goto('/settings?section=mcp');
		await expectSection(page, 'mcp');
		await expect(page.locator('#plan')).toHaveCount(0);
	});

	test('the legacy #plan link opens Plan & usage and beats ?section', async ({ page }) => {
		test.setTimeout(60000);
		await mockPrefs(page);
		await loginAsOwner(page);
		await page.goto('/settings#plan');
		await expectSection(page, 'plan');
		await page.goto('/settings?section=appearance#plan');
		await expectSection(page, 'plan');
		await page.getByRole('listbox', { name: 'Settings sections' }).getByRole('option', { name: 'MCP', exact: true }).click();
		await expectSection(page, 'mcp');
		expect(new URL(page.url()).hash).toBe('');
	});

	test('the legacy #notifications link opens Notifications', async ({ page }) => {
		test.setTimeout(60000);
		await mockPrefs(page);
		await loginAsOwner(page);
		await page.goto('/settings#notifications');
		await expectSection(page, 'notifications');
	});

	test('an unknown section falls back to Account and the Logout card is gone', async ({ page }) => {
		test.setTimeout(60000);
		await mockPrefs(page);
		await loginAsOwner(page);
		await page.goto('/settings?section=nope');
		await expectSection(page, 'account');
		const sidebar = page.getByRole('listbox', { name: 'Settings sections' });
		for (const entry of SECTIONS) {
			await sidebar.getByRole('option', { name: entry.label, exact: true }).click();
			await expectSection(page, entry.id);
			await expect(page.getByRole('button', { name: /^log ?out$/i })).toHaveCount(0);
			await expect(page.locator('main, body').first()).not.toContainText(/\bLogout\b/);
		}
	});

	test('Notifications is dropped from the sidebar when no types are configurable', async ({ page }) => {
		test.setTimeout(60000);
		await page.route('**/api/notifications/types*', (route) =>
			route.request().method() === 'GET'
				? fulfillJson(route, { success: true, data: { ...PREFS, types: [] } })
				: route.fallback()
		);
		await loginAsOwner(page);
		await page.goto('/settings?section=notifications');
		const sidebar = page.getByRole('listbox', { name: 'Settings sections' });
		await expect(sidebar.getByRole('option', { name: 'Account', exact: true })).toBeVisible({ timeout: 15000 });
		await expect(sidebar.getByRole('option', { name: 'Notifications', exact: true })).toHaveCount(0);
		await expectSection(page, 'account');
	});

	test('an admin on an unready instance sees the Resume setup banner above a non-Account section', async ({ page }) => {
		test.setTimeout(60000);
		await mockPrefs(page);
		await page.route('**/api/readiness*', (route) => fulfillJson(route, NOT_READY));
		await loginAsOwner(page);
		await page.goto('/settings?section=appearance');
		await expectSection(page, 'appearance');
		const banner = page.locator('a[href="/setup"]', { hasText: 'Resume setup' });
		await expect(banner).toBeVisible({ timeout: 15000 });
		const bannerBox = await banner.boundingBox();
		const themeBox = await page.locator('[role="radiogroup"][aria-label="Theme"]').boundingBox();
		expect(bannerBox!.y).toBeLessThan(themeBox!.y);
		await expectNoHorizontalOverflow(page);
		await screenshot(page, JOURNEY, 'resume-banner-1440');
	});

	test('a ready instance shows no Resume setup banner', async ({ page }) => {
		test.setTimeout(60000);
		await mockPrefs(page);
		await page.route('**/api/readiness*', (route) => fulfillJson(route, { overall: 'ready', checks: [] }));
		await loginAsOwner(page);
		await page.goto('/settings?section=appearance');
		await expectSection(page, 'appearance');
		await expect(page.locator('a[href="/setup"]', { hasText: 'Resume setup' })).toHaveCount(0);
	});
});

test.describe('user settings sections on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('the section select switches sections and nothing overflows sideways', async ({ page }) => {
		test.setTimeout(90000);
		await mockPrefs(page);
		await page.route('**/api/readiness*', (route) => fulfillJson(route, NOT_READY));
		await loginAsOwner(page);
		await page.goto('/settings');
		await expectSection(page, 'account');
		await expect(page.getByRole('listbox', { name: 'Settings sections' })).toBeHidden();
		const select = page.locator('select:has(option[value="plan"])');
		await expect(select).toBeVisible();
		await expectNoHorizontalOverflow(page);
		await screenshot(page, JOURNEY, 'account-390');

		for (const entry of SECTIONS.slice(1)) {
			await select.selectOption(entry.id);
			await expectSection(page, entry.id);
			await expect(page).toHaveURL(new RegExp(`/settings\\?section=${entry.id}$`));
			await expect(page.locator('a[href="/setup"]', { hasText: 'Resume setup' })).toBeVisible();
			await expectNoHorizontalOverflow(page);
			await screenshot(page, JOURNEY, `${entry.id}-390`);
		}
	});

	test('?section=mcp opens the MCP section on a phone', async ({ page }) => {
		test.setTimeout(60000);
		await mockPrefs(page);
		await loginAsOwner(page);
		await page.goto('/settings?section=mcp');
		await expectSection(page, 'mcp');
		await expect(page.locator('select:has(option[value="plan"])')).toHaveValue('mcp');
	});
});
