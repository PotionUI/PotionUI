import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'account-switcher';
const TABS_KEY = 'potionui_tabs_state';
const PASSWORD = 'e2e-password-1';

async function registry(page: Page) {
	try {
		return await page.evaluate(() => JSON.parse(localStorage.getItem('potionui_accounts') ?? 'null'));
	} catch {
		return null;
	}
}

async function activeUserId(page: Page): Promise<string | null> {
	const current = await registry(page);
	return current?.activeId ?? null;
}

async function openMenu(page: Page) {
	await page.getByRole('button', { name: 'Account menu' }).click();
	await expect(page.getByTestId('user-menu-active')).toBeVisible();
}

async function menuHeaderName(page: Page): Promise<string> {
	await openMenu(page);
	const header = page.getByTestId('user-menu-active').locator('xpath=ancestor::*[@data-testid="user-menu-identity"][1]');
	const text = (await header.innerText()).trim();
	await page.keyboard.press('Escape');
	return text;
}

test('add a second account, switch between them, keep both workspaces, follow in another tab, log out of all', async ({
	page,
	context
}) => {
	test.setTimeout(180000);

	await loginAsOwner(page);
	const ownerId = (await activeUserId(page)) as string;
	expect(ownerId, 'owner should be the active account after login').toBeTruthy();

	const token = await ownerToken(page);
	const stamp = Date.now();
	const secondName = `e2e-switch-${stamp}`;
	const created = await page.request.post('/api/users', {
		headers: { Authorization: `Bearer ${token}` },
		data: { username: secondName, email: `${secondName}@example.com`, password: PASSWORD, account_type: 'USER' }
	});
	expect(created.ok(), `user create -> ${created.status()}`).toBeTruthy();

	const ownerTabsKey = `${TABS_KEY}::${ownerId}`;
	await page.evaluate(
		({ key }) => {
			localStorage.setItem(
				key,
				JSON.stringify({
					activeTabId: 'tab-owner-marker',
					tabs: [
						{
							id: 'tab-owner-marker',
							name: 'Owner marker',
							selectedPreset: null,
							selectedMode: null,
							selectedVariant: null,
							selectedSessionId: null,
							activeGenerationId: null,
							prompt: 'owner-only prompt',
							negativePrompt: '',
							promptSegments: [],
							negativePromptSegments: [],
							formData: {}
						}
					]
				})
			);
		},
		{ key: ownerTabsKey }
	);
	await page.reload();
	await expect(page.getByRole('button', { name: 'Account menu' })).toBeVisible({ timeout: 20000 });

	await openMenu(page);
	await expect(page.getByTestId('user-menu-accounts')).toHaveCount(0);
	await page.getByTestId('user-menu-add-account').click();
	await expect(page).toHaveURL(/\/login\?add=1/);
	await expect(page.getByText('Add an account')).toBeVisible();
	await screenshot(page, JOURNEY, 'add-mode');

	await page.locator('#username').fill(secondName);
	await page.locator('#password').fill(PASSWORD);
	await page.getByRole('button', { name: 'Add account' }).click();
	await page.waitForURL(/\/generate/, { timeout: 20000 });
	await expect(page.getByRole('button', { name: 'Account menu' })).toBeVisible({ timeout: 20000 });

	const afterAdd = await registry(page);
	expect(afterAdd.accounts).toHaveLength(2);
	const secondId = afterAdd.activeId as string;
	expect(secondId).not.toBe(ownerId);
	expect(await menuHeaderName(page)).toContain(secondName);

	const secondTabsKey = `${TABS_KEY}::${secondId}`;
	const ownerTabsAfterAdd = await page.evaluate((key) => localStorage.getItem(key), ownerTabsKey);
	expect(ownerTabsAfterAdd, 'the first account keeps its tabs while the second signs in').toContain('owner-only prompt');
	const secondTabs = await page.evaluate((key) => localStorage.getItem(key), secondTabsKey);
	expect(secondTabs ?? '').not.toContain('owner-only prompt');

	const follower = await context.newPage();
	await follower.goto('/history');
	await expect(follower.getByRole('button', { name: 'Account menu' })).toBeVisible({ timeout: 20000 });
	expect(await menuHeaderName(follower)).toContain(secondName);
	await follower.evaluate(() => {
		(window as unknown as { __beforeSwitch?: boolean }).__beforeSwitch = true;
	});

	await openMenu(page);
	await expect(page.getByTestId('user-menu-accounts')).toContainText('2 / 5');
	await screenshot(page, JOURNEY, 'menu-with-accounts');
	await page
		.locator(`[data-testid="account-row"][data-user-id="${ownerId}"] button[role="menuitem"]`)
		.click();

	await expect.poll(() => activeUserId(page), { timeout: 20000 }).toBe(ownerId);
	await expect(page.getByRole('button', { name: 'Account menu' })).toBeVisible({ timeout: 20000 });
	await expect(page.getByTestId('account-switch-overlay')).toHaveCount(0, { timeout: 20000 });
	expect(await menuHeaderName(page)).toContain(OWNER_NAME());

	const ownerTabsAfterSwitchBack = await page.evaluate((key) => localStorage.getItem(key), ownerTabsKey);
	expect(ownerTabsAfterSwitchBack, 'the first account tabs survive a switch away and back').toContain(
		'owner-only prompt'
	);
	const secondTabsAfterSwitchBack = await page.evaluate((key) => localStorage.getItem(key), secondTabsKey);
	expect(secondTabsAfterSwitchBack ?? '').not.toContain('owner-only prompt');

	await expect
		.poll(() => follower.evaluate(() => (window as unknown as { __beforeSwitch?: boolean }).__beforeSwitch === undefined), {
			timeout: 20000
		})
		.toBe(true);
	await expect(follower.getByRole('button', { name: 'Account menu' })).toBeVisible({ timeout: 20000 });
	expect(await menuHeaderName(follower)).toContain(OWNER_NAME());
	await follower.close();

	await openMenu(page);
	await expect(page.getByTestId('user-menu-accounts')).toContainText(secondName);
	await page.getByTestId('user-menu-logout-all').click();
	await expect(page.getByText('Log out of all accounts?')).toBeVisible();
	await screenshot(page, JOURNEY, 'logout-all-confirm');
	await page.getByRole('button', { name: 'Log out of all', exact: true }).click();

	await page.waitForURL(/\/login/, { timeout: 20000 });
	const leftovers = await page.evaluate((prefix) => {
		const keys: string[] = [];
		for (let i = 0; i < localStorage.length; i++) {
			const key = localStorage.key(i);
			if (key) keys.push(key);
		}
		return keys.filter((key) => key === 'potionui_accounts' || key === 'auth_token' || key.startsWith(`${prefix}::`));
	}, TABS_KEY);
	expect(leftovers).toEqual([]);
});

function OWNER_NAME(): string {
	return process.env.E2E_USERNAME || 'e2e-owner';
}
