import { test, expect } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'admin-toolset';

// Per-LLM-config tool governance: an admin can disable and lock a chat tool
// for one config without affecting another config's rows for the SAME tool
// (see src.features.llm.tools.governance). Driving the chat composer's "My
// Tools" panel through a real browser session is heavier than this journey
// needs, so the user-facing effect (a locked/admin-disabled tool's opt-out
// being rejected, scoped to a named config) is asserted against the
// /api/llm/toolset/preferences API directly, as the same authenticated owner
// session that just made the admin change.
test('tool governance is scoped per LLM config, admin UI and API alike', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const stamp = Date.now();
	const configABody = {
		name: `e2e-toolset-cfg-a-${stamp}`,
		type: 'openai',
		enabled: true,
		base_url: 'https://api.example.invalid',
		model: 'gpt-4',
		system_message: 'You are a helpful assistant.'
	};
	const configBBody = { ...configABody, name: `e2e-toolset-cfg-b-${stamp}` };

	const createA = await page.request.post('/api/llm/configurations', {
		headers: { Authorization: `Bearer ${token}` },
		data: configABody
	});
	expect(createA.ok(), `config A create -> ${createA.status()}`).toBeTruthy();
	const configAId: string = (await createA.json()).data.id;

	const createB = await page.request.post('/api/llm/configurations', {
		headers: { Authorization: `Bearer ${token}` },
		data: configBBody
	});
	expect(createB.ok(), `config B create -> ${createB.status()}`).toBeTruthy();
	const configBId: string = (await createB.json()).data.id;

	await page.goto('/admin?tab=llm');

	const configList = page.locator('[role="listbox"][aria-label="LLM configurations"]');
	await expect(configList).toBeVisible({ timeout: 15000 });

	// --- Config A: disable Search Gallery, lock Get Active Models ---
	await configList.getByRole('option').filter({ hasText: configABody.name }).click();
	await page.getByRole('button', { name: 'Toolset' }).click();

	const searchGalleryRowA = page.locator('[data-testid="toolset-row"][data-tool="search_gallery"]');
	await expect(searchGalleryRowA).toBeVisible({ timeout: 15000 });
	await searchGalleryRowA.getByRole('switch', { name: 'Enabled' }).click();
	await expect(searchGalleryRowA.locator('[data-testid="tool-status-badge"]').getByText('Off', { exact: true })).toBeVisible({ timeout: 10000 });

	const activeModelsRowA = page.locator('[data-testid="toolset-row"][data-tool="get_active_models"]');
	await activeModelsRowA.getByRole('switch', { name: 'Locked' }).click();
	await expect(activeModelsRowA.locator('[data-testid="tool-status-badge"]').getByText('Locked', { exact: true })).toBeVisible({ timeout: 10000 });

	await screenshot(page, JOURNEY, 'config-a-after-changes');

	// --- Config B: untouched - same two tools must show no badges ---
	await configList.getByRole('option').filter({ hasText: configBBody.name }).click();
	await page.getByRole('button', { name: 'Toolset' }).click();

	const searchGalleryRowB = page.locator('[data-testid="toolset-row"][data-tool="search_gallery"]');
	await expect(searchGalleryRowB).toBeVisible({ timeout: 15000 });
	await expect(searchGalleryRowB.locator('[data-testid="tool-status-badge"]').getByText('Off', { exact: true })).toHaveCount(0);
	const activeModelsRowB = page.locator('[data-testid="toolset-row"][data-tool="get_active_models"]');
	await expect(activeModelsRowB.locator('[data-testid="tool-status-badge"]').getByText('Locked', { exact: true })).toHaveCount(0);

	await screenshot(page, JOURNEY, 'config-b-unaffected');

	// Reload config A: changes must have persisted server-side.
	await page.goto('/admin?tab=llm');
	await expect(configList).toBeVisible({ timeout: 15000 });
	await configList.getByRole('option').filter({ hasText: configABody.name }).click();
	await page.getByRole('button', { name: 'Toolset' }).click();
	await expect(
		page.locator('[data-testid="toolset-row"][data-tool="search_gallery"]').locator('[data-testid="tool-status-badge"]').getByText('Off', { exact: true })
	).toBeVisible({ timeout: 15000 });
	await expect(
		page.locator('[data-testid="toolset-row"][data-tool="get_active_models"]').locator('[data-testid="tool-status-badge"]').getByText('Locked', { exact: true })
	).toBeVisible({ timeout: 15000 });

	// User-facing effect, scoped per config: config A omits the disabled tool
	// and reports the locked one; config B (untouched) sees both normally.
	const prefsA = await page.request.get('/api/llm/toolset/preferences', {
		headers: { Authorization: `Bearer ${token}` },
		params: { llm_config_id: configAId }
	});
	expect(prefsA.ok()).toBeTruthy();
	const prefsABody = await prefsA.json();
	const namesA: string[] = prefsABody.data.map((t: { name: string }) => t.name);
	expect(namesA).not.toContain('search_gallery');
	const activeModelsPrefA = prefsABody.data.find((t: { name: string }) => t.name === 'get_active_models');
	expect(activeModelsPrefA?.locked).toBe(true);

	const prefsB = await page.request.get('/api/llm/toolset/preferences', {
		headers: { Authorization: `Bearer ${token}` },
		params: { llm_config_id: configBId }
	});
	expect(prefsB.ok()).toBeTruthy();
	const prefsBBody = await prefsB.json();
	const namesB: string[] = prefsBBody.data.map((t: { name: string }) => t.name);
	expect(namesB).toContain('search_gallery');
	const activeModelsPrefB = prefsBBody.data.find((t: { name: string }) => t.name === 'get_active_models');
	expect(activeModelsPrefB?.locked).toBe(false);

	// A tool locked by config A rejects the user's own opt-out attempt when
	// the toggle names config A; the same toggle succeeds against config B.
	const lockedOptOut = await page.request.put('/api/llm/toolset/preferences/get_active_models', {
		headers: { Authorization: `Bearer ${token}` },
		data: { disabled: true, llm_config_id: configAId }
	});
	expect(lockedOptOut.status()).toBe(409);

	const unlockedOptOut = await page.request.put('/api/llm/toolset/preferences/get_active_models', {
		headers: { Authorization: `Bearer ${token}` },
		data: { disabled: true, llm_config_id: configBId }
	});
	expect(unlockedOptOut.status()).toBe(200);

	// Clean up the opt-out this test itself created, so it doesn't leak into
	// another spec sharing this backend within the chunk.
	await page.request.put('/api/llm/toolset/preferences/get_active_models', {
		headers: { Authorization: `Bearer ${token}` },
		data: { disabled: false, llm_config_id: configBId }
	});
});
