import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot, apiGet, apiPost } from './helpers';
import { presetOption } from './presetPreamble';

const JOURNEY = 'cloud-fake-preset-extension';
const PLUGIN_ID = 'e2e-preset-extension';
const BACKEND_NAME = 'E2E Fake Cloud';
const PRESET_NAME = 'Fake Studio';
const MODEL_SLUG = 'fake~fake~image-1';
const FIELD = 'extension_vignette';

const field = (page: Page, name: string) => page.locator(`[data-field-name="${name}"]`);

async function openFakeStudio(page: Page) {
	await page.goto('/generate');
	const choose = page.getByRole('button', { name: 'Choose a preset' });
	const needsPreset = await choose.waitFor({ state: 'visible', timeout: 10000 }).then(() => true, () => false);
	if (needsPreset) {
		await choose.click();
		await presetOption(page, PRESET_NAME).click();
		await expect(page.getByRole('dialog', { name: 'Choose preset' })).toBeHidden();
	}
	await expect(field(page, 'model')).toBeVisible({ timeout: 20000 });
}

async function pickModel(page: Page, label: string) {
	const swap = field(page, 'model').locator('button:visible', { hasText: 'Swap' }).first();
	if (await swap.isVisible().catch(() => false)) await swap.click();
	await field(page, 'model').locator('input').first().click();
	await page.getByText(label, { exact: true }).last().click();
}

async function latestGeneration(page: Page, token: string) {
	const list = await apiGet(page, '/api/admin/generations?limit=5&sort_by=created_at&sort_dir=desc', token);
	return (list.data.generations as Array<Record<string, any>>)[0];
}

test.describe.configure({ mode: 'serial' });

test.describe('a plugin extends an existing preset mode', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('the added field renders in its tab and submits into form_data, and leaves with the plugin', async ({ page }) => {
		test.setTimeout(180000);
		await loginAsOwner(page);
		const token = await ownerToken(page);
		await apiPost(page, '/api/plugins/scan', token);
		await apiPost(page, `/api/plugins/${PLUGIN_ID}/enable`, token);
		try {
			const backends = await apiGet(page, '/api/backends', token);
			const backend = (backends.data as Array<{ id: string; name: string }>).find((b) => b.name === BACKEND_NAME);
			expect(backend, `backend "${BACKEND_NAME}" must be seeded`).toBeTruthy();
			await apiPost(page, `/api/cloud/backends/${backend!.id}/catalog/enable`, token, { slugs: [MODEL_SLUG] });

			await openFakeStudio(page);
			await pickModel(page, 'Fake Image');
			const generationTab = page.getByRole('tabpanel');
			const added = generationTab.locator(`[data-field-name="${FIELD}"]`);
			await expect(added).toBeVisible();
			await expect(added).toContainText('Vignette');
			await expect(added.getByRole('checkbox')).not.toBeChecked();
			await added.locator('label').click();
			await expect(added.getByRole('checkbox')).toBeChecked();
			await screenshot(page, JOURNEY, 'field-in-generation-tab-1440');

			const editor = page.locator('[contenteditable="true"]').first();
			await editor.click();
			await page.keyboard.type('a quiet harbor');
			const request = page.waitForRequest(
				(candidate) => candidate.method() === 'POST' && (candidate.postData() ?? '').includes('"form_data"')
			);
			await page.getByRole('button', { name: 'Generate', exact: true }).click();
			const sent = JSON.parse((await request).postData() ?? '{}');
			expect(sent.form_data[FIELD]).toBe(true);
			await expect(page.getByRole('button', { name: 'Cancel generation' })).toBeVisible({ timeout: 15000 });
			await expect(page.getByRole('button', { name: 'Generate', exact: true })).toBeVisible({ timeout: 60000 });
			await expect.poll(async () => (await latestGeneration(page, token))?.status, { timeout: 30000 }).toBe('completed');

			const generation = await latestGeneration(page, token);
			const stored = await apiGet(page, `/api/generations/history/${generation.id}`, token);
			expect(stored.data.form_data[FIELD]).toBe(true);
		} finally {
			await apiPost(page, `/api/plugins/${PLUGIN_ID}/disable`, token);
		}

		await openFakeStudio(page);
		await expect(field(page, FIELD)).toHaveCount(0);
	});
});
