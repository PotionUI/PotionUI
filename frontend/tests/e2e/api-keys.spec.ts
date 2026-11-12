import { test, expect, type Browser, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot, SIGNED_OUT } from './helpers';

const JOURNEY = 'api-keys';
const PASSWORD = 'e2e-password-1';
const SECRET = /^pui_[0-9A-Za-z]{46}$/;

type Json = Record<string, any>;

async function api(page: Page, method: 'get' | 'post' | 'put' | 'patch' | 'delete', url: string, token: string, data?: unknown) {
	const res = await page.request[method](url, { headers: { Authorization: `Bearer ${token}` }, ...(data === undefined ? {} : { data }) });
	expect(res.ok(), `${method.toUpperCase()} ${url} -> ${res.status()}`).toBeTruthy();
	return (await res.json()) as Json;
}

async function withKey(page: Page, secret: string, method: 'get' | 'post', url: string, data?: unknown) {
	return page.request[method](url, { headers: { Authorization: `Bearer ${secret}` }, ...(data === undefined ? {} : { data }) });
}

async function enableMcp(page: Page, token: string) {
	const settings = (await api(page, 'get', '/api/settings', token)).data as Json;
	await api(page, 'put', '/api/settings', token, { mcp_enabled: true, api_keys_enabled: true });
	return settings;
}

async function createKeyInUi(page: Page, name: string): Promise<string> {
	await page.goto('/settings?section=keys');
	await page.getByRole('button', { name: 'Create key' }).first().click();
	const dialog = page.getByRole('dialog', { name: 'Create API key' });
	await expect(dialog).toBeVisible({ timeout: 15000 });
	await dialog.locator('#api-key-name').fill(name);
	await screenshot(page, JOURNEY, 'create-dialog');
	await dialog.getByRole('button', { name: 'Create key' }).click();
	const shown = page.getByRole('dialog', { name: 'Key created' });
	await expect(shown).toBeVisible({ timeout: 15000 });
	const secret = ((await shown.locator('code.select-all').innerText()) ?? '').trim();
	expect(secret).toMatch(SECRET);
	return secret;
}

async function keyIdByName(page: Page, token: string, name: string): Promise<string> {
	const keys = (await api(page, 'get', '/api/keys', token)).data.keys as Array<{ id: string; name: string }>;
	const found = keys.find((key) => key.name === name);
	expect(found, `key ${name} in the list`).toBeTruthy();
	return found!.id;
}

async function secondUser(page: Page, token: string, stamp: number) {
	const username = `e2e-keys-${stamp}`;
	const created = await api(page, 'post', '/api/users', token, { username, email: `${username}@example.com`, password: PASSWORD, account_type: 'USER' });
	return { id: created.data.id as string, username };
}

async function userSession(browser: Browser, baseURL: string | undefined, username: string) {
	const context = await browser.newContext({ baseURL, storageState: SIGNED_OUT });
	const page = await context.newPage();
	await page.goto('/login');
	await page.locator('#username').fill(username);
	await page.locator('#password').fill(PASSWORD);
	await page.getByRole('button', { name: 'Sign In' }).click();
	await page.waitForURL(/\/generate/, { timeout: 15000 });
	const token = await page.evaluate(() => localStorage.getItem('auth_token'));
	expect(token, 'the user should have a session token').toBeTruthy();
	return { context, page, token: token as string };
}

test.describe('API keys, user flow', () => {
	test('create a Read key, see the secret once and copy it, use it, narrow an MCP tool, revoke it', async ({ page, context }) => {
		test.setTimeout(180000);
		await context.grantPermissions(['clipboard-read', 'clipboard-write']);
		await loginAsOwner(page);
		const token = await ownerToken(page);
		const original = await enableMcp(page, token);
		const name = `e2e-key-${Date.now()}`;

		try {
			const secret = await createKeyInUi(page, name);
			const shown = page.getByRole('dialog', { name: 'Key created' });
			await expect(shown.getByText('Copy this key now. You won’t see it again.')).toBeVisible();

			await shown.getByRole('button', { name: 'Copy key' }).click();
			await expect(shown.getByRole('button', { name: 'Copied' })).toBeVisible();
			expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(secret);
			await screenshot(page, JOURNEY, 'secret-shown');

			await shown.getByRole('button', { name: 'I have saved it' }).click();
			await expect(shown).toHaveCount(0);

			const row = page.locator('[data-key-row]').filter({ hasText: name });
			await expect(row).toBeVisible({ timeout: 15000 });
			await expect(row).toContainText('Read');
			await expect(row).toContainText('never used');
			await page.reload();
			await expect(page.locator('[data-key-row]').filter({ hasText: name })).toBeVisible({ timeout: 15000 });
			expect(await page.content()).not.toContain(secret);

			const read = await withKey(page, secret, 'get', '/api/presets');
			expect(read.status(), 'a Read key reads presets').toBe(200);
			const write = await withKey(page, secret, 'post', '/api/generations/start', {});
			expect(write.status(), 'a Read key cannot start a generation').toBe(403);

			const keyId = await keyIdByName(page, token, name);
			const before = (await api(page, 'get', `/api/mcp/keys/${keyId}/tools`, token)).data;
			const target = (before.tools as Array<{ name: string; state: string }>).find((tool) => tool.state === 'on');
			expect(target, 'a tool that is on for the new key').toBeTruthy();

			await page.getByRole('button', { name: /Default for all keys/ }).click();
			await page.getByRole('menuitemradio', { name: new RegExp(name) }).click();
			const toolRow = page.locator(`[data-tool="${target!.name}"]`);
			await page.getByRole('searchbox', { name: 'Search MCP tools' }).fill(target!.name);
			const toggle = toolRow.locator('input[role="switch"]');
			await expect(toggle).toBeChecked();
			const saved = page.waitForResponse((res) => res.url().includes(`/api/mcp/keys/${keyId}/tools`) && res.request().method() === 'PATCH');
			await toggle.click();
			expect((await saved).ok()).toBeTruthy();
			await expect(toggle).not.toBeChecked();
			await screenshot(page, JOURNEY, 'tool-narrowed');

			await page.reload();
			await page.getByRole('button', { name: /Default for all keys/ }).click();
			await page.getByRole('menuitemradio', { name: new RegExp(name) }).click();
			await page.getByRole('searchbox', { name: 'Search MCP tools' }).fill(target!.name);
			await expect(page.locator(`[data-tool="${target!.name}"] input[role="switch"]`)).not.toBeChecked();
			const after = (await api(page, 'get', `/api/mcp/keys/${keyId}/tools`, token)).data;
			expect((after.tools as Array<{ name: string; state: string }>).find((tool) => tool.name === target!.name)?.state).toBe('off');

			await page.locator('[data-key-row]').filter({ hasText: name }).getByRole('button', { name: 'Revoke key' }).click();
			const confirm = page.getByRole('alertdialog');
			await expect(confirm).toContainText(`Revoke “${name}”?`);
			await confirm.getByRole('button', { name: 'Revoke key' }).click();
			await expect(page.locator('[data-key-row]').filter({ hasText: name })).toHaveCount(0, { timeout: 15000 });

			const refused = await withKey(page, secret, 'get', '/api/presets');
			expect(refused.status(), 'a revoked key is refused on the next call').toBe(401);
		} finally {
			await api(page, 'put', '/api/settings', token, {
				mcp_enabled: original.mcp_enabled,
				api_keys_enabled: original.api_keys_enabled
			});
		}
	});

	test('a key made with Generate and an expiry shows both on its row', async ({ page }) => {
		test.setTimeout(120000);
		await loginAsOwner(page);
		const token = await ownerToken(page);
		const original = await enableMcp(page, token);
		const name = `e2e-gen-${Date.now()}`;
		try {
			await page.goto('/settings?section=keys');
			await page.getByRole('button', { name: 'Create key' }).first().click();
			const dialog = page.getByRole('dialog', { name: 'Create API key' });
			await dialog.locator('#api-key-name').fill(name);
			await dialog.getByRole('switch', { name: 'Can generate' }).click();
			await dialog.getByRole('button', { name: '30 days' }).click();
			await dialog.getByRole('button', { name: 'Create key' }).click();
			const shown = page.getByRole('dialog', { name: 'Key created' });
			const secret = ((await shown.locator('code.select-all').innerText()) ?? '').trim();
			expect(secret).toMatch(SECRET);
			await shown.getByRole('button', { name: 'I have saved it' }).click();

			const row = page.locator('[data-key-row]').filter({ hasText: name });
			await expect(row).toContainText('Generate', { timeout: 15000 });
			const listed = (await api(page, 'get', '/api/keys', token)).data.keys as Array<{ name: string; expires_at: string | null; scopes: string[] }>;
			const made = listed.find((key) => key.name === name)!;
			expect(made.expires_at).not.toBeNull();
			expect(made.scopes).toContain('generate');
			await row.getByRole('button', { name: 'Revoke key' }).click();
			await page.getByRole('alertdialog').getByRole('button', { name: 'Revoke key' }).click();
			await expect(row).toHaveCount(0, { timeout: 15000 });
		} finally {
			await api(page, 'put', '/api/settings', token, { mcp_enabled: original.mcp_enabled, api_keys_enabled: original.api_keys_enabled });
		}
	});
});

test.describe('API keys, admin flow', () => {
	test('the ceiling, a person\'s access and revoking another user\'s key all reach that user', async ({ page, browser, baseURL }) => {
		test.setTimeout(300000);
		await loginAsOwner(page);
		const token = await ownerToken(page);
		const original = await enableMcp(page, token);
		const stamp = Date.now();
		const person = await secondUser(page, token, stamp);
		const ceilingBefore = (await api(page, 'get', '/api/admin/mcp/tools', token)).data;
		let loweredTool: string | null = null;
		const session = await userSession(browser, baseURL, person.username);

		try {
			await page.goto('/admin?tab=settings&view=access');
			await expect(page.getByTestId('scope-generate')).toBeVisible({ timeout: 20000 });
			await expect(page.getByTestId('mcp-ceiling')).toBeVisible({ timeout: 20000 });
			await screenshot(page, JOURNEY, 'admin-access');

			await expect(page.getByTestId('api-keys-enabled-note')).toHaveCount(0);
			await page.getByTestId('scope-generate').click();
			await expect(page.getByTestId('scope-generate-note')).toContainText('within seconds');
			const firstOn = page.getByTestId('mcp-ceiling').locator('[data-tool]').filter({ has: page.locator('input[role="switch"]:checked') }).first();
			loweredTool = await firstOn.getAttribute('data-tool');
			expect(loweredTool).toBeTruthy();
			await firstOn.locator('input[role="switch"]').click();
			await expect(page.getByTestId('mcp-ceiling-note')).toContainText('lose any tool turned off here');
			await expect(page.getByText('2 unsaved changes')).toBeVisible();
			await screenshot(page, JOURNEY, 'admin-access-pending');

			const saved = Promise.all([
				page.waitForResponse((res) => res.url().includes('/api/settings') && res.request().method() === 'PUT'),
				page.waitForResponse((res) => res.url().includes('/api/admin/mcp/tools') && res.request().method() === 'PATCH')
			]);
			await page.getByRole('button', { name: 'Save', exact: true }).click();
			const [settingsRes, toolsRes] = await saved;
			expect(settingsRes.ok() && toolsRes.ok()).toBeTruthy();
			await expect(page.getByText('unsaved change')).toHaveCount(0);

			await session.page.goto('/settings?section=keys');
			await session.page.getByRole('button', { name: 'Create key' }).first().click();
			const dialog = session.page.getByRole('dialog', { name: 'Create API key' });
			await expect(dialog).toBeVisible({ timeout: 15000 });
			await expect(dialog.getByRole('switch', { name: 'Can generate' })).toBeDisabled();
			await expect(dialog).toContainText('Not allowed on this instance.');
			await dialog.getByRole('button', { name: 'Cancel' }).click();

			await session.page.getByRole('searchbox', { name: 'Search MCP tools' }).fill(loweredTool!);
			const locked = session.page.locator(`[data-tool="${loweredTool}"]`);
			await expect(locked).toContainText('Turned off by your administrator.');
			await expect(locked.locator('input[role="switch"]')).toBeDisabled();
			await screenshot(session.page, JOURNEY, 'user-sees-ceiling');

			const created = await api(session.page, 'post', '/api/keys', session.token, { name: `from-${person.username}`, scopes: ['read'], expires_in_days: null });
			const secret = created.data.secret as string;
			const keyId = created.data.key.id as string;
			expect((await withKey(page, secret, 'get', '/api/presets')).status()).toBe(200);

			await page.goto(`/admin?tab=users&id=${person.id}`);
			const access = page.getByTestId('user-api-access');
			await expect(access).toBeVisible({ timeout: 20000 });
			await expect(access.locator(`[data-user-key="${keyId}"]`)).toBeVisible();
			await access.getByTestId(`access-keys-${person.id}`).click();
			await expect(access.getByTestId('user-access-note')).toContainText('Nothing is deleted');
			await expect(access.getByTestId(`access-mcp-${person.id}`)).toBeDisabled();
			await expect.poll(async () => (await withKey(page, secret, 'get', '/api/presets')).status(), { timeout: 15000 }).toBe(401);

			await session.page.goto('/settings?section=keys');
			await expect(session.page.getByText('API keys are turned off by your administrator.')).toBeVisible({ timeout: 15000 });
			await expect(session.page.getByRole('button', { name: 'Create key' }).first()).toBeDisabled();

			await access.getByTestId(`access-keys-${person.id}`).click();
			await expect(access.getByTestId('user-access-note')).toHaveCount(0);
			await expect.poll(async () => (await withKey(page, secret, 'get', '/api/presets')).status(), { timeout: 15000 }).toBe(200);

			await page.goto(`/admin?tab=settings&view=api_keys&owner=${person.id}`);
			const row = page.locator(`[data-admin-key="${keyId}"]`);
			await expect(row).toBeVisible({ timeout: 20000 });
			await expect(row).toContainText(person.username);
			await screenshot(page, JOURNEY, 'admin-keys');
			await row.getByRole('button', { name: 'Revoke key' }).click();
			const confirm = page.getByRole('alertdialog');
			await expect(confirm).toContainText(`for ${person.username}?`);
			await screenshot(page, JOURNEY, 'admin-revoke-confirm');
			await confirm.getByRole('button', { name: 'Revoke key' }).click();
			await expect(row).toContainText('Revoked', { timeout: 15000 });
			expect((await withKey(page, secret, 'get', '/api/presets')).status(), 'the revoked key is refused at once').toBe(401);

			await row.getByRole('button', { name: new RegExp(`Open from-${person.username}`) }).click();
			const history = page.getByTestId('admin-key-history');
			await expect(history).toContainText('Revoked');
			await expect(history).toContainText('By administrator');
			await screenshot(page, JOURNEY, 'admin-key-history');

			await session.page.goto('/settings?section=keys');
			await session.page.getByRole('button', { name: 'Show 1 inactive' }).click();
			await expect(session.page.locator(`[data-key-row="${keyId}"]`)).toHaveAttribute('data-status', 'revoked');
		} finally {
			await api(page, 'put', '/api/settings', token, {
				api_key_scopes_allowed: original.api_key_scopes_allowed,
				api_key_max_lifetime_days: original.api_key_max_lifetime_days,
				api_keys_enabled: original.api_keys_enabled,
				mcp_enabled: original.mcp_enabled
			});
			if (loweredTool) {
				const before = (ceilingBefore.tools as Array<{ name: string; state: string; reviewed: boolean }>).find((tool) => tool.name === loweredTool);
				await api(page, 'patch', '/api/admin/mcp/tools', token, { overrides: { [loweredTool]: before && before.reviewed ? before.state === 'on' : null } });
			}
			await session.context.close();
			await page.request.delete(`/api/users/${person.id}`, { headers: { Authorization: `Bearer ${token}` } });
		}
	});

	test('the keys table filters by status and pages through every key', async ({ page }) => {
		test.setTimeout(120000);
		await loginAsOwner(page);
		await page.goto('/admin?tab=settings&view=api_keys');
		await expect(page.getByTestId('admin-api-keys')).toBeVisible({ timeout: 20000 });
		const total = Number(((await page.getByTestId('admin-keys-total').innerText()) ?? '0').trim());
		await page.getByRole('group', { name: 'Key status' }).getByRole('button', { name: 'Revoked' }).click();
		await expect(page.locator('[data-admin-key]').first().or(page.getByText('No keys match'))).toBeVisible({ timeout: 15000 });
		for (const status of await page.locator('[data-admin-key]').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-status')))) {
			expect(status).toBe('revoked');
		}
		await page.getByRole('group', { name: 'Key status' }).getByRole('button', { name: 'All' }).click();
		await page.getByRole('searchbox', { name: 'Search keys' }).fill(`no-such-key-${Date.now()}`);
		await expect(page.getByText('No keys match')).toBeVisible({ timeout: 15000 });
		await page.getByRole('searchbox', { name: 'Search keys' }).fill('');
		await expect(page.getByText('No keys match')).toHaveCount(0, { timeout: 15000 });
		if (total > 10) {
			await page.getByRole('button', { name: 'Page 2' }).click();
			await expect(page.getByTestId('admin-keys-range')).toContainText(`11–`);
		}
	});
});
