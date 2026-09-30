import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'cloud-fake-generate';
const BACKEND_NAME = 'E2E Fake Cloud';
const PRESET_NAME = 'Fake Studio';
const BEAT = 400;

async function apiGet(page: Page, url: string, token: string) {
	const res = await page.request.get(url, { headers: { Authorization: `Bearer ${token}` } });
	expect(res.ok(), `GET ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

async function backendId(page: Page, token: string): Promise<string> {
	const list = await apiGet(page, '/api/backends', token);
	const backend = (list.data as Array<{ id: string; name: string }>).find((b) => b.name === BACKEND_NAME);
	expect(backend, `backend "${BACKEND_NAME}" must be seeded`).toBeTruthy();
	return backend!.id;
}

const field = (page: Page, name: string) => page.locator(`[data-field-name="${name}"]`);

async function setKnob(page: Page, token: string, id: string, knobs: Record<string, unknown>) {
	const current = await apiGet(page, `/api/backends/${id}`, token);
	const res = await page.request.put(`/api/backends/${id}`, {
		headers: { Authorization: `Bearer ${token}` },
		data: { ...current.data, ...knobs }
	});
	expect(res.ok(), `PUT backend -> ${res.status()} ${await res.text()}`).toBeTruthy();
}

async function openFakeStudio(page: Page) {
	await page.addInitScript(() => localStorage.setItem('potionui-form-audience', 'advanced'));
	await page.goto('/generate');
	const choose = page.getByRole('button', { name: 'Choose a preset' });
	const needsPreset = await choose.waitFor({ state: 'visible', timeout: 10000 }).then(() => true, () => false);
	if (needsPreset) {
		await choose.click();
		await page.getByText(PRESET_NAME, { exact: true }).first().click();
		await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
	}
	await expect(field(page, 'model')).toBeVisible({ timeout: 20000 });
}

async function pickModel(page: Page, label: string) {
	const swap = field(page, 'model').locator('button:visible', { hasText: 'Swap' }).first();
	if (await swap.isVisible().catch(() => false)) await swap.click();
	await field(page, 'model').locator('input').first().click();
	await page.getByText(label, { exact: true }).last().click();
	await page.waitForTimeout(BEAT);
}

async function typePrompt(page: Page, text: string) {
	const editor = page.locator('[contenteditable="true"]').first();
	await editor.click();
	await page.keyboard.type(text);
	await page.waitForTimeout(BEAT);
}

async function latestGeneration(page: Page, token: string) {
	const list = await apiGet(page, '/api/admin/generations?limit=5&sort_by=created_at&sort_dir=desc', token);
	return (list.data.generations as Array<Record<string, any>>)[0];
}

test.describe.configure({ mode: 'serial' });

test.describe('cloud generation against the fake provider', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	let token = '';
	let id = '';

	test('an admin enables two models in the catalog', async ({ page }) => {
		test.setTimeout(120000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		id = await backendId(page, token);

		await page.goto(`/admin?tab=backends&backend=${id}&view=catalog`);
		await expect(page.getByRole('button', { name: 'Refresh catalog' }).first()).toBeVisible({ timeout: 20000 });
		const row = (label: string) => page.locator('.dt-scroll > .dt-row:not(.dt-row--head)', { hasText: label }).first();
		await expect(row('Fake Image')).toBeVisible({ timeout: 20000 });
		await expect(row('Fake Image')).toContainText('$0.04 / image');
		await expect(row('Fake Lite')).toContainText('$0.01 / image');

		await row('Fake Image').getByRole('switch').click();
		await expect(row('Fake Image').getByRole('switch')).toBeChecked();
		await expect(page.getByTestId('catalog-summary')).toContainText('1 enabled', { timeout: 10000 });
		await row('Fake Lite').getByRole('switch').click();
		await expect(row('Fake Lite').getByRole('switch')).toBeChecked();
		await expect(page.getByTestId('catalog-summary')).toContainText('2 enabled', { timeout: 10000 });
		await screenshot(page, JOURNEY, 'catalog-enabled-1440');
	});

	test('the form follows the chosen model', async ({ page }) => {
		test.setTimeout(120000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		await openFakeStudio(page);

		await pickModel(page, 'Fake Lite');
		await expect(field(page, 'aspect_ratio')).toBeVisible();
		await expect(field(page, 'quality')).toHaveCount(0);
		await expect(field(page, 'background')).toHaveCount(0);
		await field(page, 'aspect_ratio').locator('button[aria-haspopup="listbox"]').click();
		await expect(page.getByRole('option', { name: '1:1' })).toBeVisible();
		await expect(page.getByRole('option', { name: '4:3' })).toBeVisible();
		await expect(page.getByRole('option', { name: '16:9' })).toHaveCount(0);
		await screenshot(page, JOURNEY, 'form-fake-lite-1440');
		await page.getByRole('option', { name: '1:1' }).click();

		await pickModel(page, 'Fake Image');
		await expect(field(page, 'quality')).toBeVisible();
		await expect(field(page, 'background')).toBeVisible();
		await expect(field(page, 'count').locator('input')).toHaveValue('1');
		await field(page, 'quality').locator('button[aria-haspopup="listbox"]').click();
		await expect(page.getByRole('option', { name: '1', exact: true })).toBeVisible();
		await expect(page.getByRole('option', { name: '10', exact: true })).toBeVisible();
		await expect(page.getByRole('option', { name: '11', exact: true })).toHaveCount(0);
		await page.getByRole('option', { name: '7', exact: true }).click();
		await field(page, 'aspect_ratio').locator('button[aria-haspopup="listbox"]').click();
		await expect(page.getByRole('option', { name: '16:9' })).toBeVisible();
		await expect(page.getByRole('option', { name: '4:3' })).toHaveCount(0);
		await page.getByRole('option', { name: '1:1' }).click();
		await screenshot(page, JOURNEY, 'form-fake-image-1440');

		await page.getByRole('tab', { name: 'Provider options' }).click();
		await expect(field(page, 'provider_options')).toContainText('Style');
		await screenshot(page, JOURNEY, 'form-fake-image-provider-options-1440');
	});

	test('a generation shows progress and lands in the gallery', async ({ page }) => {
		test.setTimeout(180000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		await openFakeStudio(page);
		await pickModel(page, 'Fake Image');
		await typePrompt(page, 'a lighthouse at dusk');

		await page.getByRole('button', { name: 'Generate', exact: true }).click();
		await expect(page.getByRole('button', { name: 'Cancel generation' })).toBeVisible({ timeout: 15000 });
		await screenshot(page, JOURNEY, 'generating-1440');
		await expect(page.getByRole('button', { name: 'Generate', exact: true })).toBeVisible({ timeout: 60000 });
		await page.waitForTimeout(BEAT);
		const generation = await latestGeneration(page, token);
		expect(generation.status).toBe('completed');
		await expect
			.poll(
				() =>
					page
						.locator('img')
						.evaluateAll((images) =>
							images.filter((image) => (image as HTMLImageElement).naturalWidth >= 100 && image.getBoundingClientRect().width > 200).length
						),
				{ timeout: 15000 }
			)
			.toBeGreaterThan(0);
		await screenshot(page, JOURNEY, 'result-1440');
	});

	test('a running generation can be cancelled', async ({ page }) => {
		test.setTimeout(180000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		id = await backendId(page, token);
		await setKnob(page, token, id, { duration_seconds: 120 });
		try {
			await openFakeStudio(page);
			await pickModel(page, 'Fake Image');
			await typePrompt(page, 'a slow sunrise');
			await page.getByRole('button', { name: 'Generate', exact: true }).click();
			const cancel = page.getByRole('button', { name: 'Cancel generation' });
			await expect(cancel).toBeVisible({ timeout: 15000 });
			await page.waitForTimeout(2000);
			await cancel.click();
			await expect(page.getByRole('button', { name: 'Generate', exact: true })).toBeVisible({ timeout: 15000 });
			await screenshot(page, JOURNEY, 'cancelled-1440');
			await expect.poll(async () => (await latestGeneration(page, token)).status, { timeout: 15000 }).toBe('cancelled');
		} finally {
			await setKnob(page, token, id, { duration_seconds: 2 });
		}
	});

	test('the admin sees the cost of the completed generation', async ({ page }) => {
		test.setTimeout(120000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		await page.goto('/admin?tab=generations');
		await expect(page.getByRole('heading', { name: 'Generations' }).first()).toBeVisible({ timeout: 20000 });
		await expect(page.getByText('$0.04').first()).toBeVisible({ timeout: 15000 });
		await screenshot(page, JOURNEY, 'admin-cost-1440');
	});
});

test.describe('cloud form on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('the settings sheet follows the chosen model', async ({ page }) => {
		test.setTimeout(120000);
		await loginAsOwner(page);
		await page.addInitScript(() => localStorage.setItem('potionui-form-audience', 'advanced'));
		await page.goto('/generate');
		await page.getByRole('button', { name: 'Open preset and session' }).click();
		const sheet = page.getByRole('dialog', { name: 'Preset and session' });
		await expect(sheet).toBeVisible({ timeout: 5000 });
		const trigger = sheet.locator('button[aria-haspopup="dialog"]', { hasText: 'Choose a preset' });
		const needsPreset = await trigger.waitFor({ state: 'visible', timeout: 8000 }).then(() => true, () => false);
		if (needsPreset) {
			await trigger.click();
			await page.locator('[role="listbox"][aria-label="Presets"]').getByText(PRESET_NAME, { exact: true }).click();
			await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
		}
		await page.keyboard.press('Escape');
		await page.getByRole('button', { name: 'Settings', exact: true }).click();
		await expect(page.getByRole('dialog', { name: 'Settings' })).toBeVisible({ timeout: 5000 });
		await page.waitForTimeout(1200);
		await pickModel(page, 'Fake Lite');
		await expect(field(page, 'quality')).toHaveCount(0);
		await screenshot(page, JOURNEY, 'form-fake-lite-390');
		await pickModel(page, 'Fake Image');
		await expect(field(page, 'quality')).toBeVisible();
		await screenshot(page, JOURNEY, 'form-fake-image-390');
	});
});
