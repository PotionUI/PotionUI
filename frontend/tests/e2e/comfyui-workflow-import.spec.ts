import { test, expect, type Page } from '@playwright/test';
import { readFileSync, rmSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Admin -> Plugins -> ComfyUI Backend -> "Import workflow": the 4-step
// wizard (Source -> Inputs -> Requirements -> Done), paste the Export (API)
// fixture, walk every step, land on the created preset via "Open in
// Presets". The throwaway backend runs with cwd = the real repo checkout
// (see tests/e2e/harness/e2e_harness.py), so emit_preset writes into the
// real, .gitignored content/presets/local/ - the unique family name plus
// the cleanup below keep this journey from leaving anything behind.
test.use({ viewport: { width: 1440, height: 900 } });

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

test('Admin > Plugins > ComfyUI Backend - import a workflow through the wizard into a lint-clean preset', async ({
	page
}) => {
	// Import + reload + modify (edit -> update) + delete, ~12 screenshots and
	// several wizard round trips - comfortably over the 30s default.
	test.setTimeout(90_000);
	const familyId = `e2e-import-${Date.now()}`;
	const createdPresetDir = resolve(REPO_ROOT, 'content/presets/local', familyId);

	try {
		await loginAsOwner(page);
		const token = await ownerToken(page);

		// New plugins start disabled (src/features/plugins/operations/scan.py),
		// so a fresh throwaway instance never shows this plugin's tabs until
		// this runs.
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

		await page.goto('/admin?tab=plugins');
		await page.waitForTimeout(500);

		const pluginListRow = page.getByText('ComfyUI Backend', { exact: false }).first();
		if ((await pluginListRow.count()) === 0) {
			test.skip(
				true,
				"'ComfyUI Backend' is not present in the plugin list - the plugin frontend isn't mounted on this build " +
					'(content/plugins/local/comfyui-backend, if present, ships without a frontend and shadows the marketplace copy).'
			);
			return;
		}
		await pluginListRow.click();
		await screenshot(page, JOURNEY, '01-plugin-selected');

		const tabNav = page.locator('nav[aria-label="Plugin details"]');
		await expect(tabNav).toBeVisible();

		// Settings gets its own card background (DetailSection), same as Overview -
		// captured here for the maintainer's visual check, unrelated to the rest
		// of this journey.
		const settingsTab = tabNav.getByText('Settings', { exact: false });
		if ((await settingsTab.count()) > 0) {
			await settingsTab.click();
			await page.waitForTimeout(300);
			await screenshot(page, JOURNEY, '01b-settings-tab');
		}

		const importTab = tabNav.getByText('Import workflow', { exact: false });
		if ((await importTab.count()) === 0) {
			test.skip(true, "'Import workflow' tab not present - the admin_tabs hook wasn't picked up on this build.");
			return;
		}
		await importTab.click();

		const wizard = page.locator('[data-import-wizard]');
		await expect(wizard).toBeVisible();
		await screenshot(page, JOURNEY, '02-wizard-source');

		// Step 1: Source.
		const workflowJson = readFileSync(FIXTURE_PATH, 'utf-8');
		await wizard.locator('textarea[data-import-json-input]').fill(workflowJson);
		await wizard.locator('button[data-import-analyze]').click();

		// Step 2: Inputs.
		await expect(wizard.locator('[data-import-detected]')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, '03-wizard-inputs');
		await wizard.locator('#import-model-family').fill(familyId);
		await wizard.locator('#import-display-name').fill('E2E imported SDXL');
		await wizard.locator('button[data-import-continue-inputs]').click();

		// Step 3: Requirements (non-blocking - Continue works regardless of
		// what the checkers found against this backend-less throwaway instance).
		await expect(wizard.locator('[data-wiz-step="requirements"].current')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, '04-wizard-requirements');
		await wizard.locator('button[data-import-create]').click();

		// Step 4: Done.
		await expect(wizard.locator('[data-import-lint]')).toBeVisible({ timeout: 15000 });
		await screenshot(page, JOURNEY, '05-wizard-done');
		await expect(wizard.locator('[data-import-lint]')).toContainText('imported');

		const openLink = wizard.locator('a[data-import-open-preset]');
		await expect(openLink).toBeVisible();
		await openLink.click();

		await expect(page).toHaveURL(/tab=presets/);
		await page.waitForTimeout(500);
		await screenshot(page, JOURNEY, '06-opened-in-presets');
		await expect(page.getByText('E2E imported SDXL', { exact: false }).first()).toBeVisible({ timeout: 10000 });

		// The "Imported presets" tab lists the same preset back on the plugin page.
		await page.goto('/admin?tab=plugins');
		await page.waitForTimeout(500);
		await pluginListRow.click();
		await tabNav.getByText('Imported presets', { exact: false }).click();
		// Other leftover imported presets may already exist in this checkout's
		// content/presets/local/ (a prior run's cleanup, or a hand-imported
		// one) - scope every row action to THIS run's own row (unique family
		// id) rather than the whole table.
		const importedTable = page.locator('[data-imported-presets]');
		await expect(importedTable).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, '07-imported-presets-tab');
		await expect(page.getByText('E2E imported SDXL', { exact: false }).first()).toBeVisible();
		const myRow = importedTable.locator('.ip-row').filter({ hasText: familyId });
		await expect(myRow).toHaveCount(1);

		// Reload: re-emits from the stored source under the same id, shows an
		// inline lint-result chip.
		await myRow.locator('button[aria-label="Reload from source"]').click();
		await expect(myRow.locator('.ip-reload-result')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, '08-reload-result');

		// Modify: edit hands off to the "Import workflow" tab, prefilled from
		// this preset's stored source + sidecar field choices.
		await myRow.locator('button[aria-label="Edit"]').click();
		await expect(wizard).toBeVisible();
		await expect(wizard.locator('[data-import-editing]')).toBeVisible({ timeout: 10000 });
		await screenshot(page, JOURNEY, '09-wizard-edit-prefill');

		await wizard.locator('button[data-import-analyze]').click();
		await expect(wizard.locator('[data-wiz-step="inputs"].current')).toBeVisible();
		await wizard.locator('#import-display-name').fill('E2E imported SDXL (updated)');
		await wizard.locator('button[data-import-continue-inputs]').click();
		await expect(wizard.locator('[data-wiz-step="requirements"].current')).toBeVisible({ timeout: 10000 });

		const updateBtn = wizard.locator('button[data-import-create]');
		await expect(updateBtn).toContainText('Update preset');
		await updateBtn.click();
		await expect(wizard.locator('[data-import-lint]')).toBeVisible({ timeout: 15000 });
		await screenshot(page, JOURNEY, '10-wizard-updated');

		// Back on Imported presets: the rename stuck (same preset, same id),
		// then delete it.
		await tabNav.getByText('Imported presets', { exact: false }).click();
		await expect(page.getByText('E2E imported SDXL (updated)', { exact: false })).toBeVisible({ timeout: 10000 });
		const myRowAfterUpdate = importedTable.locator('.ip-row').filter({ hasText: familyId });
		await expect(myRowAfterUpdate).toHaveCount(1);

		await myRowAfterUpdate.locator('button[aria-label="Delete"]').click();
		const confirmDialog = page.locator('[role="dialog"][aria-label="Delete imported preset"]');
		await expect(confirmDialog).toBeVisible();
		await screenshot(page, JOURNEY, '11-delete-confirm');
		await confirmDialog.getByRole('button', { name: 'Delete', exact: true }).click();
		await expect(importedTable.locator('.ip-row').filter({ hasText: familyId })).toHaveCount(0, { timeout: 10000 });
		await screenshot(page, JOURNEY, '12-deleted');
	} finally {
		rmSync(createdPresetDir, { recursive: true, force: true });
	}
});
