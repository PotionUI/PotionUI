import { test, expect, type Page } from '@playwright/test';
import { api, brewStatus, headers, openElixir, prepare, seedElixir, setKnobs } from './elixirs-fixtures';

test.describe.configure({ mode: 'serial' });

const GENERATED = 'img[src*="/api/media/generations/"], img[src^="data:image"]';

async function openPhotoStep(page: Page, editor: ReturnType<Page['locator']>) {
	await editor.getByRole('button', { name: 'Open Potion photo in preset' }).click();
	await expect(page.getByTestId('step-edit-bar')).toBeVisible({ timeout: 15000 });
}

async function latestRunId(page: Page, token: string, elixirId: string) {
	const listed = await api(page, token, 'get', `/api/elixirs/${elixirId}/runs`);
	return listed.runs[0].id as string;
}

test('a step opened mid-brew shows its live generation in the preset view', async ({ page }) => {
	test.setTimeout(180000);
	await page.setViewportSize({ width: 1440, height: 900 });
	const { token, modelRef } = await prepare(page);
	await setKnobs(page, token, { duration_seconds: 12.0, fail_kind: '' });
	const elixirId = await seedElixir(page, token, modelRef, 'automatic', 'Live step');

	const editor = await openElixir(page, elixirId);
	await editor.locator('.elixir-header').getByRole('button', { name: 'Brew', exact: true }).click();
	await expect(brewStatus(editor)).toContainText(/Brewing|Starting/, { timeout: 15000 });
	await expect
		.poll(async () => {
			const runId = await latestRunId(page, token, elixirId);
			const run = await api(page, token, 'get', `/api/elixirs/runs/${runId}`);
			return run.invocations.some((item: any) => item.node_id === 'photo' && item.generation_id && ['submitted', 'running'].includes(item.status));
		}, { timeout: 30000 })
		.toBe(true);

	await expect(editor.getByRole('article', { name: 'Potion photo, preset step' }).getByRole('progressbar')).toBeVisible({ timeout: 30000 });

	await openPhotoStep(page, editor);
	await expect(page.locator('#workbench-container').first()).toBeVisible({ timeout: 15000 });
	await expect(page.locator('#workbench-container').locator(GENERATED).first()).toBeVisible({ timeout: 60000 });
	await expect(page.getByTestId('step-edit-bar')).toBeVisible();
	await expect
		.poll(async () => (await api(page, token, 'get', `/api/elixirs/runs/${await latestRunId(page, token, elixirId)}`)).status, { timeout: 120000 })
		.toBe('completed');
	await expect(page.locator('#workbench-container').locator('img[src*="/api/media/generations/"]').first()).toBeVisible({ timeout: 30000 });
});

test('a step opened after the brew shows its finished results without generating again', async ({ page }) => {
	test.setTimeout(180000);
	await page.setViewportSize({ width: 1440, height: 900 });
	const { token, modelRef } = await prepare(page);
	const elixirId = await seedElixir(page, token, modelRef, 'automatic', 'Finished step');

	const editor = await openElixir(page, elixirId);
	await editor.locator('.elixir-header').getByRole('button', { name: 'Brew', exact: true }).click();
	await expect(brewStatus(editor)).toHaveText('Done', { timeout: 90000 });
	const runId = await latestRunId(page, token, elixirId);
	const before = await api(page, token, 'get', `/api/elixirs/runs/${runId}`);

	await openPhotoStep(page, editor);
	await expect(page.locator('#workbench-container').locator(GENERATED).first()).toBeVisible({ timeout: 30000 });

	const after = await page.request.get(`/api/elixirs/runs/${runId}`, { headers: headers(token) });
	expect((await after.json()).invocations.length).toBe(before.invocations.length);
});
