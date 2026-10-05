import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { fixturePng } from './imageEditorHelpers';

const JOURNEY = 'auto-organize-rule-flow';
const COLLECTION = 'E2E Auto Filed';
const RULE_NAME = 'E2E images';

async function uploadSamples(page: Page, token: string, count: number) {
	for (let i = 0; i < count; i++) {
		const response = await page.request.post('/api/media/upload', {
			headers: { Authorization: `Bearer ${token}` },
			multipart: { file: { name: `auto-organize-${Date.now()}-${i}.png`, mimeType: 'image/png', buffer: fixturePng(64 + i) } }
		});
		expect(response.ok(), `upload -> ${response.status()}`).toBeTruthy();
	}
}

test('a rule made from a library filter previews, files existing uploads, shows where it came from, and can be undone', async ({ page }) => {
	test.setTimeout(180000);
	await page.setViewportSize({ width: 1440, height: 960 });
	await loginAsOwner(page);
	const token = await ownerToken(page);
	await uploadSamples(page, token, 2);

	await page.goto('/library');
	await page.getByRole('button', { name: 'Images', exact: true }).click();
	const makeRule = page.getByRole('button', { name: 'Make a rule from this filter' });
	await expect(makeRule).toBeVisible({ timeout: 15000 });
	await screenshot(page, JOURNEY, '1-filter');
	await makeRule.click();

	await expect(page).toHaveURL(/\/auto-organize\?subject=uploads&rule=new/);
	const nameInput = page.getByLabel('Rule name');
	await expect(nameInput).toBeVisible({ timeout: 15000 });
	await expect(page.locator('[data-testid="condition-row"] [data-fact="media_kind"]')).toBeVisible();
	await nameInput.fill(RULE_NAME);

	const actionRow = page.locator('[data-testid="action-row"][data-action="add_to_collection"]');
	await actionRow.getByRole('button').first().click();
	await page.getByText('Create a new collection').click();
	await actionRow.getByLabel('New collection name').fill(COLLECTION);

	const matched = page.locator('[data-testid="preview-matched"]');
	await expect(matched).toBeVisible({ timeout: 15000 });
	await expect(matched).not.toHaveText(/^0$/);
	await screenshot(page, JOURNEY, '2-editor-preview');

	await page.getByTestId('apply-existing').check();
	await page.getByRole('button', { name: 'Create', exact: true }).click();

	const progress = page.getByTestId('job-progress');
	await expect(progress).toBeVisible({ timeout: 20000 });
	await expect(progress).toHaveAttribute('data-status', 'completed', { timeout: 60000 });
	await expect(progress).toContainText(`to ${RULE_NAME}`);
	await screenshot(page, JOURNEY, '3-backfill-done');

	await page.goto('/library');
	await page.getByRole('button').filter({ has: page.locator('img') }).first().click();
	const provenance = page.getByTestId('provenance-line');
	await expect(provenance).toBeVisible({ timeout: 15000 });
	await expect(provenance).toContainText('Added by rule');
	await expect(provenance).toContainText(RULE_NAME);
	await screenshot(page, JOURNEY, '4-added-by-rule');

	await page.goto('/auto-organize?subject=uploads&view=activity');
	const run = page.getByTestId('activity-run').filter({ hasText: RULE_NAME }).first();
	await expect(run).toBeVisible({ timeout: 15000 });
	await expect(run).toContainText(`to ${COLLECTION}`);
	await run.getByRole('button', { name: 'Undo' }).click();
	await page.getByRole('button', { name: 'Confirm', exact: true }).click();
	await expect(run).toContainText('Undone', { timeout: 15000 });
	await expect(run.getByRole('button', { name: 'Undo' })).toHaveCount(0);
	await screenshot(page, JOURNEY, '5-undone');
});
