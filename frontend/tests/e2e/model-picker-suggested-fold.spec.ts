import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { installAndSelectImagePreset } from './presetPreamble';

const JOURNEY = 'model-picker-suggested-fold';
const STORAGE_KEY = 'potionui:modelPicker:suggestedOpen';

function variant(id: string, overrides: Record<string, unknown> = {}) {
	return {
		id,
		label: id,
		precision: id,
		filename: `${id}.safetensors`,
		size_bytes: 6_600_000_000,
		installed: false,
		gated: false,
		license_url: null,
		uploader: 'Comfy-Org',
		source: 'huggingface',
		repo_id: 'Comfy-Org/Model',
		source_url: 'https://huggingface.co/Comfy-Org/Model',
		is_recipe_default: false,
		fast: true,
		recommended: false,
		note: null,
		...overrides
	};
}

async function mockVariants(page: Page, variants: Array<Record<string, unknown>>) {
	await page.unroute('**/api/recipes/variants/preset/**');
	await page.route('**/api/recipes/variants/preset/**', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				gpu: {},
				slots: variants.length
					? [
							{
								id: 'main',
								label: 'Krea-2 Turbo (DiT)',
								kind: 'checkpoint',
								model_type: 'checkpoint',
								required: true,
								variants,
								recommended_variant_id: variants[0].id,
								reason: 'Fits your GPU',
								recipe_id: 'starter',
								recipe_name: 'Starter',
								suggested_variant_id: variants[0].id,
								suggested_reason: 'Fits your GPU'
							}
						]
					: []
			})
		})
	);
}

async function renderedRows(page: Page): Promise<number> {
	return (
		(await page.locator('#model-picker-suggested [data-slot-variant]').count()) +
		(await page.locator('#model-picker-suggested [data-recommended-download]').count())
	);
}

async function badgeText(page: Page): Promise<string> {
	const text = (await page.locator('[data-picker-suggested-toggle]').innerText()).replace(/suggested/i, '');
	return text.trim();
}

async function reopen(page: Page) {
	await page.reload();
	await page.waitForLoadState('networkidle');
	const chooser = page.locator('button[aria-haspopup="dialog"]', { hasText: 'Choose a preset' });
	if (await chooser.isVisible().catch(() => false)) await installAndSelectImagePreset(page);
}

async function openPicker(page: Page) {
	if ((page.viewportSize()?.width ?? 1440) < 768) {
		await page.getByRole('button', { name: 'Settings', exact: true }).first().click();
	}
	const input = page.locator('input[placeholder^="Select"]').first();
	await expect(input).toBeVisible({ timeout: 20000 });
	await input.click();
}

const VIEWPORTS = [
	{ name: '1440', width: 1440, height: 900 },
	{ name: '390', width: 390, height: 844 }
];

test('Suggested is a flat, counted fold that opens on first run and is remembered', async ({ page }) => {
	test.setTimeout(240000);
	await loginAsOwner(page);
	await mockVariants(page, [variant('int8', { label: 'Standard', size_bytes: 12_570_000_000 }), variant('nvfp4', { label: 'Low VRAM', size_bytes: 7_150_000_000 })]);
	const preset = await installAndSelectImagePreset(page);
	test.skip(!preset, 'no installable native image preset on this instance');

	for (const viewport of VIEWPORTS) {
		await page.setViewportSize({ width: viewport.width, height: viewport.height });
		await mockVariants(page, [variant('int8', { label: 'Standard', size_bytes: 12_570_000_000 }), variant('nvfp4', { label: 'Low VRAM', size_bytes: 7_150_000_000 })]);
		await page.evaluate((key) => localStorage.removeItem(key), STORAGE_KEY);
		await reopen(page);
		await openPicker(page);

		const toggle = page.locator('[data-picker-suggested-toggle]');
		await expect(toggle).toBeVisible({ timeout: 15000 });
		await expect(toggle).toHaveAttribute('aria-expanded', 'true');
		await expect(page.locator('#model-picker-suggested [data-slot-variant="int8"]')).toBeVisible();
		await expect(page.locator('[data-picker-other-variants-toggle]')).toHaveCount(0);
		const rows = await renderedRows(page);
		expect(rows).toBeGreaterThanOrEqual(2);
		expect(await badgeText(page)).toBe(String(rows));
		await screenshot(page, JOURNEY, `flat-expanded-${viewport.name}`);

		await toggle.focus();
		await page.keyboard.press('Enter');
		await expect(toggle).toHaveAttribute('aria-expanded', 'false');
		await expect(page.locator('#model-picker-suggested')).toBeHidden();
		expect(await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY)).toBe('0');
		await screenshot(page, JOURNEY, `flat-collapsed-${viewport.name}`);

		await mockVariants(page, []);
		await reopen(page);
		await openPicker(page);
		await expect(page.getByText('No model installed').first()).toBeVisible({ timeout: 15000 });
		await page.waitForTimeout(1500);
		const recommendedRows = await page.locator('#model-picker-suggested [data-recommended-download]').count();
		if (recommendedRows > 0) {
			expect(await badgeText(page)).toBe(String(recommendedRows));
			await screenshot(page, JOURNEY, `recommended-only-${viewport.name}`);
		} else {
			await expect(toggle).toHaveCount(0);
			await screenshot(page, JOURNEY, `none-${viewport.name}`);
		}
	}
});
