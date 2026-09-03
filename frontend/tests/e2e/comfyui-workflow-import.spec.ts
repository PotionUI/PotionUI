import { test, expect, type Page } from '@playwright/test';
import { readFileSync, rmSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Admin -> Presets -> "Import ComfyUI workflow": paste the Export (API)
// fixture, analyze, create, see the lint result, open the new preset. The
// throwaway backend runs with cwd = the real repo checkout (see
// tests/e2e/harness/e2e_harness.py), so emit_preset writes into the real,
// .gitignored content/presets/local/ - the unique family name plus the
// cleanup below keep this journey from leaving anything behind.
const JOURNEY = 'comfyui-workflow-import';
const PLUGIN_ID = 'comfyui-backend';
const REPO_ROOT = resolve(__dirname, '../../..');
const FIXTURE_PATH = resolve(
	REPO_ROOT,
	'content/plugins/marketplace/comfyui-backend/tests/fixtures/sdxl_basic_api.json'
);

async function apiGet(page: Page, url: string, token: string) {
	const res = await page.request.get(url, { headers: { Authorization: `Bearer ${token}` } });
	expect(res.ok(), `GET ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

async function apiPost(page: Page, url: string, token: string, data?: unknown) {
	const res = await page.request.post(url, {
		headers: { Authorization: `Bearer ${token}` },
		data: data ?? {}
	});
	expect(res.ok(), `POST ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

test('Admin > Presets - import a ComfyUI workflow into a lint-clean preset', async ({ page }) => {
	const familyId = `e2e-import-${Date.now()}`;
	const createdPresetDir = resolve(REPO_ROOT, 'content/presets/local', familyId);

	try {
		await loginAsOwner(page);
		const token = await ownerToken(page);

		// Discover + enable the plugin through the real admin API, matching the
		// fe90-mode-row-krea2 journey's approach - new plugins start disabled
		// (src/features/plugins/operations/scan.py), so a fresh throwaway
		// instance never shows the import action until this runs.
		await apiPost(page, '/api/plugins/scan', token);
		const pluginsList = await apiGet(page, '/api/plugins', token);
		const pluginRow = (pluginsList.data || []).find((p: any) => p.id === PLUGIN_ID);
		if (!pluginRow) {
			test.skip(true, `'${PLUGIN_ID}' was not discovered on this throwaway instance.`);
			return;
		}
		if (!pluginRow.enabled) {
			await apiPost(page, `/api/plugins/${PLUGIN_ID}/enable`, token);
		}

		await page.goto('/admin?tab=presets');
		await page.waitForTimeout(500);

		const importTrigger = page.getByRole('button', { name: 'Import ComfyUI workflow' });
		if ((await importTrigger.count()) === 0) {
			test.skip(
				true,
				"'Import ComfyUI workflow' action not present - the plugin frontend isn't mounted on this build " +
					'(content/plugins/local/comfyui-backend, if present, ships without a frontend and shadows the marketplace copy).'
			);
			return;
		}
		await screenshot(page, JOURNEY, '01-presets-tab');

		await importTrigger.click();
		const dialog = page.getByRole('dialog', { name: 'Import ComfyUI workflow' });
		await expect(dialog).toBeVisible();

		const workflowJson = readFileSync(FIXTURE_PATH, 'utf-8');
		await dialog.locator('textarea[data-import-json-input]').fill(workflowJson);
		await screenshot(page, JOURNEY, '02-pasted');

		await dialog.getByRole('button', { name: 'Analyze' }).click();
		await expect(dialog.locator('[data-import-detected]')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, '03-analyzed');

		await dialog.locator('#import-model-family').fill(familyId);
		await dialog.locator('#import-display-name').fill('E2E imported SDXL');

		await dialog.getByRole('button', { name: 'Create preset' }).click();
		await expect(dialog.locator('[data-import-lint]')).toBeVisible({ timeout: 15000 });
		await screenshot(page, JOURNEY, '04-lint-result');
		await expect(dialog.locator('[data-import-lint]')).toContainText('imported');

		const openBtn = dialog.getByRole('button', { name: 'Open in Presets' });
		await expect(openBtn).toBeVisible();
		await openBtn.click();
		await expect(dialog).toBeHidden();
		await page.waitForTimeout(500);
		await screenshot(page, JOURNEY, '05-opened-in-presets');
		await expect(page.getByText('E2E imported SDXL', { exact: false }).first()).toBeVisible({ timeout: 10000 });
	} finally {
		rmSync(createdPresetDir, { recursive: true, force: true });
	}
});
