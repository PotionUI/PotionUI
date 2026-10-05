import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken } from './helpers';

const PRESET_ID = '01KX47H3MINIMAX000000000VA';
const PRESET_NAME = 'MiniMax-H3';
const MODE = 'refs';
const TAB_ID = 'schema-recovery-tab';
const TABS_STORAGE_KEY = 'potionui_tabs_state';
const SEED_FLAG = 'schema-recovery-seeded';

async function prepareHost(page: Page, token: string): Promise<string> {
	const headers = { Authorization: `Bearer ${token}` };
	const list = await page.request.get('/api/presets?include_uninstalled=true', { headers });
	const presets = ((await list.json()).data || []) as Array<{ id: string; name: string; installed?: boolean }>;
	const preset = presets.find((p) => p.id === PRESET_ID) || presets.find((p) => p.name === PRESET_NAME);
	test.skip(!preset, 'MiniMax-H3 preset is not available on this instance');
	if (!preset!.installed) {
		const res = await page.request.post(`/api/presets/${preset!.id}/install`, { headers });
		expect(res.ok(), `install -> ${res.status()}`).toBeTruthy();
	}
	const me = await page.request.get('/api/auth/me', { headers });
	const userId = (await me.json()).data.id as string;
	const assign = await page.request.post(`/api/presets/${preset!.id}/assign`, {
		headers,
		data: { user_ids: [userId] }
	});
	expect(assign.ok(), `assign -> ${assign.status()}`).toBeTruthy();
	return preset!.id;
}

test.describe('phone Settings sheet after a failed form load', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 390, height: 844 });
		await loginAsOwner(page);
	});

	test('fills in the References tab by itself once the form request recovers', async ({ page }) => {
		test.setTimeout(120000);
		const presetId = await prepareHost(page, await ownerToken(page));

		await page.addInitScript(
			({ key, flag, tab }) => {
				try {
					if (sessionStorage.getItem(flag)) return;
					sessionStorage.setItem(flag, '1');
					localStorage.setItem(key, JSON.stringify({ tabs: [tab], activeTabId: tab.id }));
				} catch {}
			},
			{
				key: TABS_STORAGE_KEY,
				flag: SEED_FLAG,
				tab: {
					id: TAB_ID,
					name: 'Schema recovery',
					selectedPreset: presetId,
					selectedMode: MODE,
					selectedVariant: null,
					selectedSessionId: null,
					activeGenerationId: null,
					prompt: '',
					negativePrompt: '',
					promptSegments: [],
					negativePromptSegments: [],
					formData: {}
				}
			}
		);

		let failed = 0;
		await page.route(/\/api\/presets\/[^/]+\/form(\?|$)/, async (route) => {
			if (failed < 3) {
				failed += 1;
				await route.fulfill({ status: 503, contentType: 'application/json', body: '{"success":false,"error":"busy"}' });
				return;
			}
			await route.continue();
		});

		await page.goto('/generate');
		await expect(page.locator('.studio-dock')).toBeVisible({ timeout: 20000 });
		await page.getByRole('button', { name: 'Settings', exact: true }).click();
		const sheet = page.getByRole('dialog', { name: 'Settings' });
		await expect(sheet).toBeVisible({ timeout: 10000 });

		await expect(sheet.getByRole('tab', { name: 'References', exact: true })).toBeVisible({ timeout: 30000 });
		expect(failed).toBeGreaterThan(0);
	});
});
