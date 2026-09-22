import { test, expect } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'prompt-manual-create';

// Prompt Library "New prompt" opens the same detail pane form used for
// editing, in a create mode - no separate composer modal. A prompt typed by
// hand (never imported) must persist and show up in the list, exactly like
// an imported one does.

test('a manually authored prompt saves and appears in the list', async ({ page }) => {
	test.setTimeout(60000);
	await loginAsOwner(page);

	await page.goto('/prompts');
	await expect(page.getByRole('button', { name: 'New prompt' }).first()).toBeVisible({
		timeout: 15000
	});

	await page.getByRole('button', { name: 'New prompt' }).first().click();
	await expect(page.getByRole('heading', { name: 'New prompt' })).toBeVisible({ timeout: 10000 });

	const uniqueName = `E2E manual prompt ${Date.now()}`;
	await page.getByPlaceholder('Content preview is used when unnamed').fill(uniqueName);
	const segmentEditor = page.locator('.inline-chip-editor[role="textbox"]').first();
	await expect(segmentEditor).toBeVisible({ timeout: 10000 });
	await segmentEditor.click();
	await page.keyboard.type('a hand-typed manual prompt, not imported');

	await page.waitForTimeout(300);
	await screenshot(page, JOURNEY, 'filled-before-save');

	const createResponse = page.waitForResponse(
		(r) => r.url().includes('/api/prompts') && r.request().method() === 'POST',
		{ timeout: 15000 }
	);

	const createButton = page.getByRole('button', { name: 'Create', exact: true });
	await expect(createButton).toBeEnabled();
	await createButton.click();

	const response = await createResponse;
	expect(response.ok(), `POST /api/prompts -> ${response.status()}`).toBeTruthy();
	const body = await response.json();
	expect(body.success, `create must succeed: ${JSON.stringify(body)}`).toBeTruthy();
	expect(
		body.data?.segments?.[0]?.content,
		`saved segment content must carry the typed text: ${JSON.stringify(body.data)}`
	).toContain('a hand-typed manual prompt, not imported');

	await expect(page.getByText('Prompt created')).toBeVisible({ timeout: 5000 });
	await expect(page.getByRole('heading', { name: uniqueName })).toBeVisible({ timeout: 10000 });

	await page.getByRole('button', { name: 'Back to Prompts' }).click();
	await expect(page.getByText(uniqueName)).toBeVisible({ timeout: 10000 });

	await page.waitForTimeout(300);
	await screenshot(page, JOURNEY, 'saved-in-list');

	const listResponse = page.waitForResponse(
		(r) => r.url().includes('/api/prompts') && r.url().includes('source_provider=manual') && r.request().method() === 'GET',
		{ timeout: 15000 }
	);
	await page.getByRole('button', { name: 'Filters' }).click();
	await page.getByRole('dialog', { name: 'Filters' }).getByRole('button', { name: 'Mine', exact: true }).click();
	await listResponse;
	await expect(page).toHaveURL(/source=mine/);
	await expect(
		page.getByText(uniqueName),
		'a hand-typed prompt must still be found once the Source filter is set to "Mine"'
	).toBeVisible({ timeout: 10000 });

	console.log(`[${JOURNEY}] manual prompt created via POST ${response.status()}, visible in list and under the Manual source filter`);
});
