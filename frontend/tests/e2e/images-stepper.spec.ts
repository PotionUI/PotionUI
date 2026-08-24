import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot, shotPath } from './helpers';

// Images-count stepper — the batch-count field ("quantity") renders as a
// compact bordered [-][value][+] control (`type: "stepper"`, still
// `NumberInput.svelte` under the hood) instead of a range slider. Evidence:
// open SDXL's txt2img form, screenshot the stepper, click + twice, screenshot
// the bumped value, and assert the bound value actually changed.

const JOURNEY = 'images-stepper';
const BEAT = 400;

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

test('images stepper — SDXL batch count renders and steps as a compact control', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const me = await apiGet(page, '/api/auth/me', token);
	const userId = me.data.id as string;

	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{
		id: string;
		name: string;
		engine?: string;
		category?: string;
		installed?: boolean;
	}>;
	const sdxlPreset =
		presets.find((p) => /sdxl/i.test(p.name)) ||
		presets.find((p) => p.engine === 'native' && p.category === 'image');

	if (!sdxlPreset) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}

	if (!sdxlPreset.installed) {
		await apiPost(page, `/api/presets/${sdxlPreset.id}/install`, token);
	}
	await apiPost(page, `/api/presets/${sdxlPreset.id}/assign`, token, { user_ids: [userId] });

	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByText(sdxlPreset.name, { exact: true }).first().click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();

	const tablist = page.locator('[role="tablist"]').first();
	await expect(tablist).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(BEAT);

	// The Images stepper lives on the "Generation" tab (first tab, icon-only).
	const generationTab = page.locator('[role="tab"]').first();
	await generationTab.click();
	await page.waitForTimeout(BEAT);

	const imagesLabel = page.getByText('Images', { exact: true }).first();
	await expect(imagesLabel).toBeVisible({ timeout: 10000 });

	const stepperCard = imagesLabel.locator('xpath=ancestor::*[contains(@class, "field-card")][1]');
	await expect(stepperCard).toBeVisible();

	const valueInput = stepperCard.locator('input[type="number"]').first();
	await expect(valueInput).toBeVisible();
	await expect(valueInput).toHaveValue('1');

	await stepperCard.scrollIntoViewIfNeeded();
	await screenshot(page, JOURNEY, '01-stepper-initial');
	await stepperCard.screenshot({ path: shotPath(JOURNEY, '02-stepper-card-initial') });

	const incrementButton = stepperCard.getByRole('button', { name: 'Increase value' });
	await incrementButton.click();
	await page.waitForTimeout(BEAT);
	await incrementButton.click();
	await page.waitForTimeout(BEAT);

	await expect(valueInput).toHaveValue('3');

	await screenshot(page, JOURNEY, '03-stepper-after-increment');
	await stepperCard.screenshot({ path: shotPath(JOURNEY, '04-stepper-card-value-3') });

	console.log(`[${JOURNEY}] Images stepper bumped 1 -> 3 on ${sdxlPreset.id}`);
});
