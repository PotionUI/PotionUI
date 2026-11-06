import { test, expect, type Page } from '@playwright/test';
import { screenshot } from './helpers';
import { api, clearElixirs, headers, openElixir, prepare, seedElixir, setKnobs } from './elixirs-fixtures';

const JOURNEY = 'elixirs-brew-history';

test.describe.configure({ mode: 'serial' });

async function startTwoBrews(page: Page, token: string, elixirId: string) {
	const editor = await openElixir(page, elixirId);
	const first = page.waitForRequest((request) => request.method() === 'POST' && request.url().endsWith(`/api/elixirs/${elixirId}/runs`));
	await editor.locator('.elixir-header').getByRole('button', { name: 'Brew', exact: true }).click();
	const body = JSON.parse((await first).postData() ?? '{}');
	await expect(editor.getByLabel('Brew progress')).toContainText('Brew 1', { timeout: 30000 });
	const second = await page.request.post(`/api/elixirs/${elixirId}/runs`, {
		headers: headers(token),
		data: { ...body, idempotency_key: `e2e-second-${Date.now()}` }
	});
	expect(second.ok(), await second.text()).toBeTruthy();
	return openElixir(page, elixirId);
}

test('two running brews: stop the older one from Brew history, then its outputs can be deleted', async ({ page }) => {
	test.setTimeout(240000);
	await page.setViewportSize({ width: 1440, height: 900 });
	const { token, modelRef } = await prepare(page);
	await clearElixirs(page, token);
	await setKnobs(page, token, { duration_seconds: 120.0 });
	const elixirId = await seedElixir(page, token, modelRef, 'automatic', 'Brew history');

	const editor = await startTwoBrews(page, token, elixirId);
	const header = editor.locator('.elixir-header');
	await expect(header.getByRole('button', { name: /^Brews 2, 2 active$/ })).toBeVisible({ timeout: 30000 });
	await screenshot(page, JOURNEY, 'header-two-active');

	await header.getByRole('button', { name: /^Brews/ }).click();
	await page.getByTestId('view-all-brews').click();
	const dialog = page.getByTestId('brew-history');
	await expect(dialog).toBeVisible();
	await expect(dialog.getByTestId('brew-row')).toHaveCount(2);
	await expect(dialog.getByTestId('result-count')).toContainText('2 brews');
	await screenshot(page, JOURNEY, 'history-two-active');

	const older = await api(page, token, 'get', `/api/elixirs/${elixirId}/runs?limit=50`);
	const olderRun = older.runs.find((run: any) => run.number === 1);
	expect(olderRun).toBeTruthy();
	const detail = await api(page, token, 'get', `/api/elixirs/runs/${olderRun.id}`);
	const generationIds: string[] = (detail.invocations ?? []).map((item: any) => item.generation_id).filter(Boolean);

	await dialog.getByRole('button', { name: 'Stop Brew 1' }).click();
	await expect(dialog.locator('[data-testid="brew-row"][data-number="1"]')).toHaveAttribute('data-status', /cancelling|cancelled/, { timeout: 30000 });
	await expect(dialog.locator('[data-testid="brew-row"][data-number="1"]')).toHaveAttribute('data-status', 'cancelled', { timeout: 60000 });
	await expect(dialog.locator('[data-testid="brew-row"][data-number="2"]')).toHaveAttribute('data-status', /running|pending/);
	await screenshot(page, JOURNEY, 'history-older-stopped');

	for (const id of generationIds) {
		const response = await page.request.delete(`/api/generations/history/${id}`, { headers: headers(token) });
		expect(response.status(), `delete ${id}`).toBeLessThan(400);
	}

	await dialog.getByRole('button', { name: /^Stop all running/ }).click();
	await page.getByRole('button', { name: /^Stop 1 brews?/ }).click();
	await expect(dialog.locator('[data-testid="brew-row"][data-number="2"]')).toHaveAttribute('data-status', 'cancelled', { timeout: 60000 });
});

test('filters and paging over seeded brews', async ({ page }) => {
	test.setTimeout(240000);
	await page.setViewportSize({ width: 1440, height: 900 });
	const { token, modelRef } = await prepare(page);
	await clearElixirs(page, token);
	await setKnobs(page, token, { duration_seconds: 0.2 });
	const elixirId = await seedElixir(page, token, modelRef, 'automatic', 'Brew paging');

	let editor = await openElixir(page, elixirId);
	const captured = page.waitForRequest((request) => request.method() === 'POST' && request.url().endsWith(`/api/elixirs/${elixirId}/runs`));
	await editor.locator('.elixir-header').getByRole('button', { name: 'Brew', exact: true }).click();
	const body = JSON.parse((await captured).postData() ?? '{}');
	await expect.poll(async () => (await api(page, token, 'get', `/api/elixirs/${elixirId}/runs?limit=1`)).active, { timeout: 60000 }).toBe(0);

	for (let index = 0; index < 54; index += 1) {
		const response = await page.request.post(`/api/elixirs/${elixirId}/runs`, {
			headers: headers(token),
			data: { ...body, idempotency_key: `e2e-seed-${index}-${Date.now()}` }
		});
		expect(response.ok(), await response.text()).toBeTruthy();
		await expect.poll(async () => (await api(page, token, 'get', `/api/elixirs/${elixirId}/runs?limit=1`)).active, { timeout: 60000 }).toBe(0);
	}

	editor = await openElixir(page, elixirId);
	const header = editor.locator('.elixir-header');
	await expect(header.getByRole('button', { name: /^Brews 55/ })).toBeVisible({ timeout: 30000 });
	await header.getByRole('button', { name: /^Brews/ }).click();
	await expect(page.locator('#elixir-brews ul li')).toHaveCount(5);
	await page.getByTestId('view-all-brews').click();

	const dialog = page.getByTestId('brew-history');
	await expect(dialog.getByTestId('brew-row')).toHaveCount(50);
	await expect(dialog.getByTestId('paging')).toContainText('Showing 1-50 of 55');
	await dialog.getByRole('navigation', { name: 'Pagination' }).getByRole('button', { name: 'Page 2', exact: true }).click();
	await expect(dialog.getByTestId('brew-row')).toHaveCount(5);
	await expect(dialog.getByTestId('paging')).toContainText('Showing 51-55 of 55');
	await screenshot(page, JOURNEY, 'history-page-two');

	await dialog.getByRole('radio', { name: 'Failed' }).click();
	await expect(dialog.getByText('No brews match')).toBeVisible();
	await dialog.getByRole('button', { name: 'Clear filters' }).first().click();
	await expect(dialog.getByTestId('brew-row')).toHaveCount(50);

	await dialog.getByRole('textbox', { name: 'Search brews' }).fill('7');
	await expect(dialog.getByTestId('result-count')).toContainText('match');
});
