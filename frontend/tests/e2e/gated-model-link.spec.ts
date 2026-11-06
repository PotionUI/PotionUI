import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, screenshot, fulfillJson } from './helpers';
import { installAndSelectImagePreset } from './presetPreamble';

const JOURNEY = 'gated-model-link';

const GATED = {
	name: 'ltx-gated-transformer.safetensors',
	installed: false,
	provider: 'huggingface',
	ref: '{"repo": "Lightricks/LTX-2.5", "file": "diffusion_models/ltx.safetensors", "revision": "main"}',
	size: '20.03 GB',
	gated: true,
	license_url: 'https://huggingface.co/Lightricks/LTX-2.5'
};

const PLAIN = {
	name: 'plain-model.safetensors',
	installed: false,
	provider: 'civitai',
	ref: '{"model_id": "1", "version_id": "2"}',
	size: '2 GB'
};

function injectRecommendations(node: unknown): void {
	if (Array.isArray(node)) {
		node.forEach(injectRecommendations);
		return;
	}
	if (!node || typeof node !== 'object') return;
	const record = node as Record<string, any>;
	const config = record.configuration;
	if (config && typeof config === 'object' && 'model_type' in config) {
		config.recommendations = [GATED, PLAIN];
	}
	Object.values(record).forEach(injectRecommendations);
}

async function mockPickerBackend(page: Page) {
	await page.route('**/api/presets/*/form*', async (route) => {
		const response = await route.fetch();
		const body = await response.json();
		injectRecommendations(body);
		await route.fulfill({ response, json: body });
	});
	await page.route('**/api/recipes/variants/preset/**', (route) => fulfillJson(route, { gpu: {}, slots: [] }));
	await page.route('**/api/models/downloads', (route) => {
		if (route.request().method() !== 'POST') return route.fallback();
		return fulfillJson(route, { success: true, data: { download_id: 'gated-dl' } });
	});
	await page.route('**/api/models/downloads/gated-dl', (route) =>
		fulfillJson(route, {
			success: true,
			data: {
				status: 'failed',
				progress: null,
				error: "Access denied (HTTP 403) fetching 'org/name' - this repo is gated or private.",
				gated: true,
				gated_repo: 'org/name'
			}
		})
	);
}

async function openPicker(page: Page) {
	if ((page.viewportSize()?.width ?? 1440) < 768) {
		await page.getByRole('button', { name: 'Settings', exact: true }).first().click();
	}
	const input = page.locator('input[placeholder^="Select"]').first();
	await expect(input).toBeVisible({ timeout: 20000 });
	await input.click();
	const toggle = page.locator('[data-picker-suggested-toggle]');
	await expect(toggle).toBeVisible({ timeout: 20000 });
	if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
}

function rowFor(page: Page, name: string) {
	return page.locator('[data-recommended-download]', { hasText: name });
}

test('a gated recommendation links to Hugging Face and a failed gated download does too', async ({ page }) => {
	test.setTimeout(240000);
	await loginAsOwner(page);
	await mockPickerBackend(page);
	const preset = await installAndSelectImagePreset(page);
	test.skip(!preset, 'no installable native image preset on this instance');

	await openPicker(page);

	const gatedRow = rowFor(page, GATED.name);
	await expect(gatedRow).toBeVisible({ timeout: 20000 });
	const link = gatedRow.locator('a[data-gated-link]');
	await expect(link).toHaveAttribute('href', GATED.license_url);
	await expect(link).toHaveAttribute('target', '_blank');
	await expect(link).toHaveAttribute('rel', 'noopener noreferrer');
	await expect(link).toContainText('Open on Hugging Face');
	await expect(rowFor(page, PLAIN.name).locator('a[data-gated-link]')).toHaveCount(0);
	await screenshot(page, JOURNEY, 'recommendation-gated');

	await rowFor(page, PLAIN.name).getByRole('button', { name: 'Download' }).click();
	const failedLink = rowFor(page, PLAIN.name).locator('a[data-gated-link]');
	await expect(failedLink).toBeVisible({ timeout: 15000 });
	await expect(failedLink).toHaveAttribute('href', 'https://huggingface.co/org/name');
	await expect(rowFor(page, PLAIN.name)).toContainText('Access denied (HTTP 403)');
	await screenshot(page, JOURNEY, 'download-failed-gated');
});
